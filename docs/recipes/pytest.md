# Recipe: pytest plugin

Assert "no prompt regressions" inside the test suite you already run. Two
routes: the plugin, which turns each case into its own test item, and
`assert_no_regressions()`, a plain function for suites that do not want a
plugin.

## The plugin

The plugin is installed with the package and auto-loaded by pytest. It is
inert until you pass `--promptdrift`: no options, no items, no output and no
files written otherwise.

```bash
pytest --promptdrift outputs.jsonl
```

Scoring is offline (`compare_outputs`), so no model is called and no key is
needed. For every case in the file the plugin adds one test item named
`promptdrift::outputs.jsonl::<case_id>` that fails when the case is a
regression (the judge said regressed, a check went from pass to fail, or the
drift level is `regression`), plus one `promptdrift::outputs.jsonl::summary`
item that fails when the file-level thresholds are exceeded.

```text
$ pytest --promptdrift examples/outputs.jsonl -q
........................................................................ [ 22%]
...
.............................F...F                                       [100%]
=================================== FAILURES ===================================
___________________________ promptdrift case percent ___________________________
promptdrift regression in percent (A vs B)
case percent
  reason: check pass_to_fail (regex)
  drift:  1.0000 (regression)
  judge:  not judged
  flip:   pass_to_fail
  A: 30
  B: 15% of 200 is 3.
_____________________________ promptdrift summary ______________________________
promptdrift summary for A vs B: 5 case(s), avg drift 0.4587, 1 regression(s)
  - 1 regression(s) exceed max-regressions 0
=========================== short test summary info ============================
FAILED promptdrift::examples/outputs.jsonl::percent - promptdrift regression ...
FAILED promptdrift::examples/outputs.jsonl::summary - promptdrift summary for...
```

The failure message is printed as is, without a traceback.

### Options

| Option | Meaning |
|---|---|
| `--promptdrift PATH` | JSONL or JSON array of logged outputs to score. Repeatable; each file gets its own items and summary |
| `--promptdrift-max-drift FLOAT` | fail the summary item when average drift exceeds this (0.0 to 1.0) |
| `--promptdrift-max-regressions INT` | fail the summary item when regressions exceed this (default 0) |
| `--promptdrift-checks FILE` | default deterministic checks (JSON or YAML) for cases without their own |
| `--promptdrift-json PATH` | save the JSON report |
| `--promptdrift-html PATH` | save the self-contained HTML report |

The thresholds use the same `evaluate_thresholds()` as the CLI and the
Action, so "exceeded" means the same thing everywhere. With several
`--promptdrift` files the JSON and HTML paths are numbered (`report-1.json`,
`report-2.json`); a single file keeps the path as given.

```bash
pytest --promptdrift outputs.jsonl --promptdrift-max-drift 0.2 --promptdrift-max-regressions 0 --junit-xml junit.xml
pytest --promptdrift a.jsonl --promptdrift b.jsonl --promptdrift-checks checks.yaml --promptdrift-json report.json --promptdrift-html report.html
```

### Selecting, marking, reporting

Items carry the `promptdrift` marker and behave like any other test:

```bash
pytest -m promptdrift --promptdrift outputs.jsonl          # only the generated items
pytest -m "not promptdrift" --promptdrift outputs.jsonl    # everything but them
pytest -k refund --promptdrift outputs.jsonl               # by case id
```

With `--junit-xml` each item appears with the case id as the test name and
`promptdrift.<file>` as the classname, so CI test reporters show regressed
cases natively.

### The fixture

`promptdrift_report` gives the `DiffReport` of the first `--promptdrift`
file, or `None` when the option was not passed, so you can write your own
assertions next to the generated items:

```python
def test_billing_cases_are_stable(promptdrift_report):
    if promptdrift_report is None:
        return
    billing = [c for c in promptdrift_report.cases if c.tags and "billing" in c.tags]
    assert all(c.drift_level != "regression" for c in billing)
```

### Loading and disabling

The entry point loads the plugin automatically. It also works with
`-p promptdrift.pytest_plugin` or `pytest_plugins = ["promptdrift.pytest_plugin"]`
in a `conftest.py`, and `-p no:promptdrift` disables it. A missing or
malformed outputs file is a usage error (pytest exit code 4). pytest 7, 8
and 9 are supported.

## Without the plugin: `assert_no_regressions`

For suites that prefer a plain function (or run on a pytest that cannot load
plugins), `assert_no_regressions` raises an `AssertionError` whose message
lists every failed threshold and every regressed case:

```python
from promptdrift import compare_outputs, assert_no_regressions

def test_prompt_v2_does_not_regress():
    report = compare_outputs("outputs.jsonl", version_a="v1", version_b="v2")
    assert_no_regressions(report, max_drift=0.2)        # max_regressions=0 by default
```

Parameters: `max_drift` (a fraction, `0.2` is 20 percent), `max_regressions`
(default 0), `max_format_changes`, `max_flips` (pass-to-fail check flips);
`None` disables a gate. On the five-record example file the message reads:

```text
promptdrift: v1 vs v2 failed 2 threshold(s)
  - avg drift 45.9% exceeds max-drift 20.0%
  - 1 regression(s) exceed max-regressions 0
regressed cases:
  case percent
    reason: check pass_to_fail (regex)
    drift:  1.0000 (regression)
    judge:  not judged
    flip:   pass_to_fail
    A: 30
    B: 15% of 200 is 3.
```

`examples/ci/test_no_regressions.py` in the repository is a complete file
using only the public API (its two `xfail` markers exist because the example
data regresses on purpose; drop them in your own suite):

```bash
# docs-test: run
python -m pytest examples/ci/test_no_regressions.py -q -p no:cacheprovider
```

## Which route?

| | Plugin | `assert_no_regressions` |
|---|---|---|
| One failing test per regressed case, visible in `--junit-xml` | yes | no, one assertion for the file |
| Needs a code change | no, a command line flag | a short test function |
| Thresholds | drift and regressions | drift, regressions, format changes, flips |
| Works with | pytest 7 to 9 | any runner, including unittest |

Both score offline. For a live comparison inside a test, call `compare()`
with a mocked provider as `examples/live/mock_provider.py` does, and pass the
report to `assert_no_regressions`.
