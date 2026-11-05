"""
Watch mode: re-run a command whenever one of its input files changes.

    promptdrift watch diff outputs.jsonl --summary
    promptdrift watch --interval 0.5 --clear compare v1.yaml v2.yaml --cases golden.json
    promptdrift watch --once diff outputs.jsonl      # one run, then exit (tests, CI)

The watcher is pure Python: it polls the modification time and size of each
input file (default every second), so it needs no extra dependency. When the
optional ``watchdog`` package is importable (``pip install promptdrift[watch]``)
it is used to wake the poll loop as soon as the operating system reports a
change, which cuts the reaction time below the polling interval; the decision
about what changed is still taken from the poll snapshot, so both backends
behave the same way. Files that do not exist yet are tolerated and watched
for creation.

``watch_files`` is public so programs and tests can drive the loop with a
``threading.Event``::

    from promptdrift.watch import watch_files

    stop = threading.Event()
    watch_files(["golden.json"], lambda changed: ..., interval=0.2, stop_event=stop)
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Union

__all__ = ["watch_files", "snapshot", "changed_paths", "run_wrapped", "run_watch",
           "COST_WARNING", "CLEAR_SCREEN"]

#: Printed before the first run when the wrapped command calls a model on every change.
COST_WARNING = ("watch re-runs call the model on every change; "
                "use --no-judge or offline diff to limit cost")
#: ANSI sequence used by ``--clear``: wipe the screen and move the cursor home.
CLEAR_SCREEN = "\033[2J\033[H"
#: Shortest accepted polling interval, in seconds.
MIN_INTERVAL = 0.05

PathLike = Union[str, "os.PathLike[str]"]


def snapshot(paths: Iterable[PathLike]) -> dict[str, Optional[tuple[int, int]]]:
    """Map each path to ``(mtime_ns, size)``, or ``None`` when it does not exist."""
    out: dict[str, Optional[tuple[int, int]]] = {}
    for p in paths:
        key = str(p)
        try:
            st = os.stat(key)
        except OSError:
            out[key] = None
        else:
            out[key] = (st.st_mtime_ns, st.st_size)
    return out


def changed_paths(before: dict, after: dict) -> list[str]:
    """Paths whose snapshot entry differs between two ``snapshot`` results."""
    return [p for p in after if before.get(p) != after[p]]


class _WatchdogWaker:
    """Wakes the poll loop early through ``watchdog``; ``None`` when unavailable."""

    def __init__(self, paths: Sequence[str], stop: threading.Event):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        self._stop_event = stop
        self.event = threading.Event()
        waker = self.event

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # pragma: no cover - exercised through a fake
                waker.set()

        self.observer = Observer()
        dirs = {str(Path(p).resolve().parent) for p in paths}
        for d in sorted(dirs):
            if os.path.isdir(d):
                self.observer.schedule(_Handler(), d, recursive=False)
        self.observer.start()

    def wait(self, timeout: float) -> None:
        """Block until an OS event, the timeout or the stop event, whichever is first."""
        deadline = time.monotonic() + timeout
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.event.wait(min(remaining, 0.2)):
                break
        self.event.clear()

    def stop(self) -> None:
        try:
            self.observer.stop()
            self.observer.join(1.0)
        except Exception:  # pragma: no cover - best effort shutdown
            pass


def _make_waker(paths: Sequence[str], backend: str, stop: threading.Event):
    if backend == "poll":
        return None
    try:
        return _WatchdogWaker(paths, stop)
    except Exception:
        if backend == "watchdog":
            raise
        return None


def watch_files(
    paths: Iterable[PathLike],
    callback: Callable[[list[str]], object],
    interval: float = 1.0,
    once: bool = False,
    stop_event: Optional[threading.Event] = None,
    backend: str = "auto",
) -> int:
    """Run ``callback`` now and again whenever one of ``paths`` changes.

    ``callback`` receives the list of paths that changed (empty on the first
    run). ``interval`` is the polling interval in seconds (floored at 0.05).
    With ``once`` the callback runs a single time and the function returns.
    ``stop_event`` ends the loop when set, from the callback or another thread;
    Ctrl-C (``KeyboardInterrupt``) ends it too. ``backend`` is ``"auto"``
    (watchdog when importable, else polling), ``"poll"`` or ``"watchdog"``.
    Returns the number of times the callback ran.
    """
    watched = [str(p) for p in paths]
    stop = stop_event if stop_event is not None else threading.Event()
    interval = max(float(interval), MIN_INTERVAL)
    runs = 0

    before = snapshot(watched)
    callback([])
    runs += 1
    if once or stop.is_set():
        return runs

    waker = _make_waker(watched, backend, stop)
    try:
        while not stop.is_set():
            if waker is not None:
                waker.wait(interval)
                if stop.is_set():
                    break
                # let an editor finish an atomic save before looking
                stop.wait(MIN_INTERVAL)
            else:
                stop.wait(interval)
            if stop.is_set():
                break
            after = snapshot(watched)
            changed = changed_paths(before, after)
            before = after
            if changed:
                callback(changed)
                runs += 1
    except KeyboardInterrupt:
        pass
    finally:
        if waker is not None:
            waker.stop()
    return runs


def run_wrapped(func: Callable[[], object]) -> int:
    """Call ``func`` and turn ``sys.exit`` into a return code instead of exiting."""
    try:
        func()
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 0


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def run_watch(
    func: Callable[[], object],
    paths: Iterable[PathLike],
    label: str = "command",
    interval: float = 1.0,
    clear: bool = False,
    once: bool = False,
    warning: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    out=None,
    backend: str = "auto",
) -> int:
    """Drive ``func`` through ``watch_files`` with status lines on ``out``.

    Each run is wrapped in ``run_wrapped`` so a ``sys.exit`` inside ``func``
    (for example ``--fail-on-regression``) only shows up in the status line.
    ``warning`` is printed once before the first run. Returns the exit code of
    the last run, which the caller can use with ``once``; a Ctrl-C returns 0.
    """
    stream = out if out is not None else sys.stdout
    watched = [str(p) for p in paths]
    last = {"code": 0}

    def callback(changed: list[str]) -> None:
        if clear:
            stream.write(CLEAR_SCREEN)
        if changed:
            stream.write(f"  watch [{_stamp()}] changed: {', '.join(changed)}\n")
        stream.flush()
        t0 = time.perf_counter()
        code = run_wrapped(func)
        elapsed = time.perf_counter() - t0
        last["code"] = code
        stream.write(f"  watch [{_stamp()}] {label} finished in {elapsed:.2f}s, exit {code}\n")
        if not once:
            stream.write(f"  watch watching {len(watched)} file(s) every "
                         f"{max(float(interval), MIN_INTERVAL):g}s, Ctrl-C to stop\n")
        stream.flush()

    if warning:
        stream.write(f"  watch warning: {warning}\n")
        stream.flush()
    try:
        watch_files(watched, callback, interval=interval, once=once,
                    stop_event=stop_event, backend=backend)
    except KeyboardInterrupt:  # raised inside the first run, before the loop
        pass
    if once:
        return last["code"]
    stream.write(f"  watch [{_stamp()}] stopped\n")
    stream.flush()
    return 0
