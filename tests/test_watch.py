"""Tests for ``promptdrift watch``: the file watcher, the wrapper and the CLI."""
import io
import sys
import threading
import types
from pathlib import Path
from unittest import mock

import pytest

from promptdrift.cli import main
from promptdrift.watch import (
    CLEAR_SCREEN,
    COST_WARNING,
    changed_paths,
    run_watch,
    run_wrapped,
    snapshot,
    watch_files,
)

from .conftest import FakeProvider

# the package re-exports compare(), so the module object must come from sys.modules
compare_module = sys.modules["promptdrift.compare"]

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "outputs.jsonl"
CHECKS = ROOT / "examples" / "offline" / "checks.yaml"
LIVE = ROOT / "examples" / "live"


# ── snapshot helpers ───────────────────────────────────────────────────────────

def test_snapshot_tolerates_missing_files_and_detects_creation(tmp_path):
    f = tmp_path / "later.json"
    before = snapshot([f])
    assert before[str(f)] is None
    f.write_text("[]")
    after = snapshot([f])
    assert after[str(f)] is not None
    assert changed_paths(before, after) == [str(f)]
    assert changed_paths(after, snapshot([f])) == []


def test_changed_paths_sees_size_and_mtime(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("one")
    before = snapshot([f])
    f.write_text("one more")
    assert changed_paths(before, snapshot([f])) == [str(f)]


# ── watch_files ────────────────────────────────────────────────────────────────

def test_watch_files_once_runs_callback_once(tmp_path):
    calls = []
    runs = watch_files([tmp_path / "x"], calls.append, once=True)
    assert runs == 1 and calls == [[]]


def test_watch_files_reruns_on_mutation_and_stops_on_event(tmp_path):
    f = tmp_path / "cases.json"
    f.write_text("[]")
    stop = threading.Event()
    calls = []

    def callback(changed):
        calls.append(changed)
        if len(calls) == 1:
            # mutate from another thread after the first run, like an editor would
            threading.Timer(0.05, lambda: f.write_text("[1, 2, 3]")).start()
        else:
            stop.set()

    runs = watch_files([f], callback, interval=0.02, stop_event=stop, backend="poll")
    assert runs == 2
    assert calls == [[], [str(f)]]


def test_watch_files_waits_for_a_file_to_be_created(tmp_path):
    f = tmp_path / "missing.jsonl"
    stop = threading.Event()
    seen = []

    def callback(changed):
        seen.append(changed)
        if changed:
            stop.set()

    threading.Timer(0.05, lambda: f.write_text("{}")).start()
    runs = watch_files([f], callback, interval=0.02, stop_event=stop, backend="poll")
    assert runs == 2 and seen[-1] == [str(f)]


def test_watch_files_returns_when_stop_event_already_set(tmp_path):
    stop = threading.Event()
    stop.set()
    assert watch_files([tmp_path / "x"], lambda c: None, stop_event=stop, backend="poll") == 1


def test_watch_files_keyboard_interrupt_exits_cleanly(tmp_path):
    f = tmp_path / "a"
    f.write_text("1")
    calls = []

    def callback(changed):
        calls.append(changed)
        if not changed:
            f.write_text("12")
        else:
            raise KeyboardInterrupt
    runs = watch_files([f], callback, interval=0.02, backend="poll")
    assert runs == 1 and len(calls) == 2


class _FakeObserver:
    """Minimal stand-in for ``watchdog.observers.Observer``."""
    instances = []

    def __init__(self):
        self.scheduled = []
        self.started = False
        self.stopped = False
        _FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass


def _install_fake_watchdog(monkeypatch):
    pkg = types.ModuleType("watchdog")
    events = types.ModuleType("watchdog.events")
    observers = types.ModuleType("watchdog.observers")

    class FileSystemEventHandler:
        def on_any_event(self, event):
            pass

    events.FileSystemEventHandler = FileSystemEventHandler
    observers.Observer = _FakeObserver
    pkg.events, pkg.observers = events, observers
    for name, mod in (("watchdog", pkg), ("watchdog.events", events),
                      ("watchdog.observers", observers)):
        monkeypatch.setitem(sys.modules, name, mod)
    _FakeObserver.instances.clear()


def test_watch_files_uses_watchdog_when_importable(tmp_path, monkeypatch):
    _install_fake_watchdog(monkeypatch)
    f = tmp_path / "golden.json"
    f.write_text("[]")
    stop = threading.Event()
    calls = []

    def poke():
        f.write_text("[42]")
        handler = _FakeObserver.instances[0].scheduled[0][0]
        handler.on_any_event(None)   # the OS event wakes the loop before the interval

    def callback(changed):
        calls.append(changed)
        if not changed:
            threading.Timer(0.05, poke).start()
        else:
            stop.set()

    runs = watch_files([f], callback, interval=5.0, stop_event=stop)
    assert runs == 2 and calls[1] == [str(f)]
    obs = _FakeObserver.instances[0]
    assert obs.started and obs.stopped
    assert obs.scheduled[0][1] == str(tmp_path.resolve())


def test_watch_files_stop_during_watchdog_wait(tmp_path, monkeypatch):
    _install_fake_watchdog(monkeypatch)
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    assert watch_files([tmp_path / "x"], lambda c: None, interval=5.0, stop_event=stop) == 1
    assert _FakeObserver.instances[0].stopped


def test_watch_files_backend_choices(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "watchdog", None)   # make the import fail
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    # auto falls back to polling silently and a stop set while waiting ends the loop
    assert watch_files([tmp_path / "x"], lambda c: None, interval=0.02, stop_event=stop) == 1

    # an explicit watchdog request is an error
    stop2 = threading.Event()
    calls = []

    def cb(changed):
        calls.append(changed)
        threading.Timer(0.01, stop2.set).start()
    with pytest.raises(ImportError):
        watch_files([tmp_path / "x"], cb, interval=0.02, stop_event=stop2, backend="watchdog")
    assert calls == [[]]   # the first run happens before the backend is chosen


# ── run_wrapped / run_watch ────────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected", [(None, 0), (0, 0), (3, 3), ("boom", 1)])
def test_run_wrapped_turns_sys_exit_into_a_code(code, expected):
    def func():
        sys.exit(code)
    assert run_wrapped(func) == expected


def test_run_wrapped_returns_zero_without_exit():
    assert run_wrapped(lambda: None) == 0


def test_run_watch_once_reports_exit_code_and_timing():
    out = io.StringIO()
    code = run_watch(lambda: sys.exit(1), [], label="diff", once=True, out=out,
                     warning=COST_WARNING)
    text = out.getvalue()
    assert code == 1
    assert COST_WARNING in text
    assert "diff finished in" in text and "exit 1" in text
    assert "Ctrl-C" not in text and "stopped" not in text
    assert CLEAR_SCREEN not in text


def test_run_watch_clear_and_continuous_stop(tmp_path):
    out = io.StringIO()
    stop = threading.Event()
    seen = []

    def func():
        seen.append(1)
        stop.set()      # a stop requested during the first run ends the loop
    code = run_watch(func, [tmp_path / "a"], label="compare", interval=0.1, clear=True,
                     stop_event=stop, out=out, backend="poll")
    text = out.getvalue()
    assert code == 0 and seen == [1]
    assert text.startswith(CLEAR_SCREEN)
    assert "compare finished in" in text and "exit 0" in text
    assert "watching 1 file(s) every 0.1s, Ctrl-C to stop" in text
    assert text.rstrip().endswith("stopped")


def test_run_watch_prints_changed_paths(tmp_path):
    f = tmp_path / "in.jsonl"
    f.write_text("{}")
    out = io.StringIO()
    stop = threading.Event()
    n = {"runs": 0}

    def func():
        n["runs"] += 1
        if n["runs"] == 1:
            threading.Timer(0.05, lambda: f.write_text('{"a": 1}')).start()
        else:
            stop.set()
    run_watch(func, [f], interval=0.02, stop_event=stop, out=out, backend="poll")
    assert f"changed: {f}" in out.getvalue()
    assert n["runs"] == 2


# ── CLI ───────────────────────────────────────────────────────────────────────

def test_cli_watch_once_diff_end_to_end(capsys):
    main(["watch", "--once", "diff", str(EXAMPLE), "--summary", "--checks", str(CHECKS)])
    out = capsys.readouterr().out
    assert "diff finished in" in out and "exit 0" in out
    assert "watch [" in out
    assert COST_WARNING not in out       # offline diff calls no model
    assert "Ctrl-C" not in out


def test_cli_watch_once_propagates_fail_on_regression(capsys):
    with pytest.raises(SystemExit) as e:
        main(["watch", "--once", "diff", str(EXAMPLE), "--summary", "--fail-on-regression"])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "exit 1" in out and "DO NOT SHIP" in out


def test_cli_watch_warns_for_diff_with_judge(capsys, no_keys):
    # without any API key the judge is skipped, so this calls no model
    main(["watch", "--once", "diff", str(EXAMPLE), "--summary", "--judge"])
    out = capsys.readouterr().out
    assert COST_WARNING in out


def test_cli_watch_warns_for_compare_and_survives_errors(capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["watch", "--once", "compare", str(tmp_path / "v1.yaml"),
              str(tmp_path / "v2.yaml"), "--cases", str(tmp_path / "golden.json"), "--no-judge"])
    assert e.value.code == 1
    captured = capsys.readouterr()
    assert COST_WARNING in captured.out
    assert "File not found" in captured.err
    assert "compare finished in" in captured.out and "exit 1" in captured.out


def test_cli_watch_compare_with_mocked_provider(capsys, tmp_path):
    checks = tmp_path / "checks.json"
    checks.write_text('{"regex": "."}')
    with mock.patch.object(compare_module, "get_provider",
                           lambda name, **kw: FakeProvider(["same answer"])):
        main(["watch", "--once", "compare", str(LIVE / "v1.yaml"), str(LIVE / "v2.yaml"),
              "--cases", str(LIVE / "golden.json"), "--no-judge", "--quiet", "--summary",
              "--bootstrap", "0", "--checks", str(checks)])
    out = capsys.readouterr().out
    assert COST_WARNING in out
    assert "compare finished in" in out and "exit 0" in out
    assert "SAFE TO SHIP" in out


def test_cli_watch_rejects_bad_interval(capsys):
    with pytest.raises(SystemExit) as e:
        main(["watch", "--interval", "0", "--once", "diff", str(EXAMPLE)])
    assert e.value.code == 2
    assert "--interval must be a positive" in capsys.readouterr().err


def test_cli_watch_rejects_bad_wrapped_args(capsys):
    with pytest.raises(SystemExit) as e:
        main(["watch", "--once", "diff"])     # missing outputs path
    assert e.value.code == 2


def test_cli_watch_continuous_mode_stops_on_ctrl_c(capsys, monkeypatch):
    import promptdrift.cli as cli_module
    original = cli_module.cmd_diff

    def interrupting(args):
        original(args)
        raise KeyboardInterrupt
    monkeypatch.setattr(cli_module, "cmd_diff", interrupting)
    main(["watch", "diff", str(EXAMPLE), "--summary"])      # returns, exit code 0
    out = capsys.readouterr().out
    assert "stopped" in out


# ── review fixes: errors other than SystemExit keep the loop alive ────────────

def test_run_wrapped_turns_an_exception_into_exit_1():
    err = io.StringIO()

    def boom():
        raise RuntimeError("half\nsaved")
    assert run_wrapped(boom, err=err) == 1
    assert err.getvalue() == "  watch error: RuntimeError: half saved\n"


def test_run_wrapped_writes_to_stderr_by_default(capsys):
    assert run_wrapped(lambda: [][1]) == 1
    assert "watch error: IndexError" in capsys.readouterr().err


def test_run_wrapped_lets_ctrl_c_through():
    def interrupt():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run_wrapped(interrupt)


def test_run_watch_survives_a_half_saved_yaml_checks_file(tmp_path, capsys):
    checks = tmp_path / "checks.yaml"
    checks.write_text("regex: [\n")  # mid-edit: not valid YAML
    out = io.StringIO()
    stop = threading.Event()
    codes = []

    def func():
        try:
            main(["diff", str(EXAMPLE), "--summary", "--bootstrap", "0",
                  "--checks", str(checks)])
        except SystemExit as e:
            codes.append(e.code)
            if len(codes) == 1:
                threading.Timer(0.05, lambda: checks.write_text('regex: "."\n')).start()
            raise
        codes.append(0)
        stop.set()
    code = run_watch(func, [EXAMPLE, checks], interval=0.02, stop_event=stop, out=out,
                     backend="poll")
    assert code == 0 and codes == [1, 0]
    text = out.getvalue()
    assert "exit 1" in text and "exit 0" in text and text.rstrip().endswith("stopped")
    assert "Input error" in capsys.readouterr().err


def test_cli_diff_invalid_yaml_checks_is_a_one_line_input_error(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("regex: [")
    with pytest.raises(SystemExit) as e:
        main(["diff", str(EXAMPLE), "--checks", str(bad)])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "Input error" in err and "invalid YAML" in err and "Traceback" not in err


def test_cli_diff_other_errors_exit_1_without_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["diff", str(EXAMPLE), "--checks", str(tmp_path)])  # a directory
    assert e.value.code == 1
    assert "Error" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["diff", str(EXAMPLE), "--checks", str(tmp_path), "--verbose"])
    assert "Traceback" in capsys.readouterr().err
