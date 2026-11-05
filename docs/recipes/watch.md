# Recipe: watch mode

`promptdrift watch` wraps `compare` or `diff` and re-runs it whenever one of
the input files changes. Keep it in a second terminal while you edit a
prompt, a case file or a check spec, and read the verdict after every save.

## Usage

```bash
promptdrift watch [--interval SECONDS] [--clear] [--once] <compare|diff> <arguments...>
```

The watch flags go **before** the command name; every flag of `compare` and
`diff` works after it, unchanged.

```bash
promptdrift watch diff outputs.jsonl --summary                                    # offline, free, re-runs on save
promptdrift watch --clear --interval 0.5 diff outputs.jsonl --checks checks.yaml --html report.html
promptdrift watch compare v1.yaml v2.yaml --cases golden.json --no-judge         # prints a cost warning first
promptdrift watch --once diff outputs.jsonl --fail-on-regression                 # one run, exit code propagated
```

| Flag | Meaning |
|---|---|
| `--interval SECONDS` | polling interval (default 1.0, floored at 0.05) |
| `--clear` | clear the terminal before each run |
| `--once` | run once and exit with the wrapped command's exit code |

Each run ends with a status line:

```bash
# docs-test: run
promptdrift watch --once diff examples/outputs.jsonl --summary
```

```text
  watch [02:00:47] diff finished in 0.00s, exit 0
```

## What is watched

| Command | Watched inputs |
|---|---|
| `compare` | both config files, the cases file, the checks file |
| `diff` | the outputs file, the checks file |

Output files (`--html`, `--output`, `--csv`, `--junit`) are never watched, so
a run cannot trigger itself. A file that does not exist yet is watched for
creation.

## Behaviour worth knowing

- **Regressions never stop the loop.** A failed gate shows as `exit 1` in the
  status line and the watcher waits for the next change. Ctrl-C exits with
  code 0.
- **`--once` is the scriptable form.** It runs the wrapped command once and
  exits with its code, so `--fail-on-regression` and the `--max-*` flags
  behave as in the plain command. Useful in tests and in CI steps that want
  the watch parser.
- **Cost warning.** Before the first run of any `compare` (generation calls
  the model even with `--no-judge`) and of `diff --judge`, the watcher prints
  `watch re-runs call the model on every change; use --no-judge or offline diff to limit cost`.
  A plain `diff` prints nothing because it calls no model. While iterating on
  a live prompt, keep the case file small and add the judge only for the
  final run.
- **Backends.** The default is pure Python polling of each file's
  modification time and size. `pip install promptdrift[watch]` installs
  `watchdog`, which wakes the loop as soon as the OS reports a change; the
  decision about what changed is still taken from the poll, so the two
  backends behave identically.

## A prompt-editing session

```bash
# terminal 1
promptdrift watch --clear compare prompts/v1.yaml prompts/v2.yaml --cases prompts/golden.json --no-judge --summary

# terminal 2: edit prompts/v2.yaml, save, read terminal 1
```

Or, free and instant, on logged outputs while you tune the checks:

```bash
promptdrift watch --clear diff prompts/outputs.jsonl --checks prompts/checks.yaml --html report.html
```

Keep `report.html` open in a browser and reload after each save for the
side-by-side diffs.

## Python

The watcher is public for programs and tests:

```python
import threading
from promptdrift.watch import watch_files

stop = threading.Event()

def on_change(changed):          # [] on the first run, then the paths that changed
    print("changed:", changed)
    if changed == ["golden.json"]:
        stop.set()

watch_files(["golden.json", "v2.yaml"], on_change, interval=0.5, stop_event=stop)
```

See [`promptdrift.watch`](../reference/api.md#promptdrift.watch).
