# Verdicts and exit codes

Every output format (terminal, HTML, JUnit, Markdown comment, Action output)
derives its verdict from one rule, `promptdrift.reporter.verdict_for(report)`,
so they never disagree.

## The rule

| Verdict | Status key | When |
|---|---|---|
| **SAFE TO SHIP** | `safe` | no regressions, average drift below 5 percent, no format change, no order-inconsistent judge verdict |
| **REVIEW** | `review` | no regressions, but average drift of 5 percent or more, or at least one format change, or at least one order-inconsistent judge verdict |
| **DO NOT SHIP** | `do_not_ship` | at least one regression |

A **regression** is a case where the judge said `regressed` or a
deterministic check went from pass to fail. An **improvement** is a case where
the judge said `improved` or a check went from fail to pass, and the case is
not a regression. Everything else is **neutral**. `inconsistent` and `error`
judge verdicts are neutral for counting purposes but trigger REVIEW.

The reason string tells you which branch fired, for example
`no regressions, minimal drift`, `semantic drift, 3 format change(s), no regressions`
or `2 regression(s) detected`.

```python
from promptdrift.reporter import verdict_for, VERDICT_LABEL

status, reason = verdict_for(report)       # ('do_not_ship', '2 regression(s) detected')
print(VERDICT_LABEL[status], reason)       # DO NOT SHIP 2 regression(s) detected
```

## The verdict is not the exit code

By default the CLI exits 0 whatever the verdict: the report is information.
Gates turn it into an exit code:

| Flag | Exits 1 when | Message |
|---|---|---|
| `--fail-on-regression` | any case regressed | none (v0.2.0 behaviour, kept silent for existing scripts) |
| `--max-regressions N` | more than N cases regressed | `2 regression(s) exceed max-regressions 0` |
| `--max-drift X` | average drift is above X (a fraction: `0.1` is 10 percent) | `avg drift 17.9% exceeds max-drift 10.0%` |
| `--max-format-changes N` | more than N cases changed format signature | `3 format change(s) exceed max-format-changes 0` |
| `--max-flips N` | more than N checks flipped from pass to fail | `2 check flip(s) from pass to fail exceed max-flips 0` |

The count limits are inclusive: `--max-regressions 2` allows two regressions
and fails on the third. Each breached threshold prints one sentence to
stderr; all of them are evaluated, so one run shows every gate that failed:

```bash
promptdrift diff examples/offline/outputs.jsonl --checks examples/offline/checks.yaml --summary --max-drift 0.10 --max-regressions 0 --max-flips 0
```

```text
  ✗ avg drift 17.9% exceeds max-drift 10.0%
  ✗ 2 regression(s) exceed max-regressions 0
  ✗ 2 check flip(s) from pass to fail exceed max-flips 0
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | run finished and every gate passed (or no gate was set) |
| 1 | a gate failed, or the run raised an error (bad file, missing key for a live run) |
| 2 | argument error from the parser |

`promptdrift watch` is the exception: in continuous mode the loop never exits
on a failed gate (the exit code is shown in the status line) and Ctrl-C exits
0; `watch --once` propagates the wrapped command's exit code. See
[Watch mode](../recipes/watch.md).

## The same gates everywhere

The thresholds are implemented once, in
`promptdrift.thresholds.evaluate_thresholds()`, and every entry point calls it:

| Entry point | How to set the gates |
|---|---|
| CLI | the `--max-*` flags above |
| GitHub Action | `max-drift`, `max-regressions`, `max-format-changes`, `max-flips` inputs, plus `fail-on-regression` (default `"true"`) |
| pytest plugin | `--promptdrift-max-drift`, `--promptdrift-max-regressions` (default 0) on the per-file summary item; every regressed case is also its own failing item |
| Python | `assert_no_regressions(report, max_drift=..., max_regressions=0, max_format_changes=..., max_flips=...)` raises `AssertionError` |
| Python, custom | `evaluate_thresholds(report_or_dict, ...)` returns the list of failure sentences, empty when all pass |

`evaluate_thresholds` and `render_markdown` also accept the dict written by
`--output`, so a saved `report.json` can be gated later without re-running
anything. That is how the Action evaluates thresholds from the report file.

## Choosing thresholds

- Start with `--max-regressions 0` (or the default `--fail-on-regression`).
  A pass-to-fail check flip or a judge verdict of regressed is worth a human
  look every time.
- Pick `--max-drift` from your own data, not from this page: run the
  comparison on a change you consider harmless and read `avg_drift` and its
  [interval](confidence.md). TF-IDF drift for a reworded prompt is often
  15 to 40 percent; a tight `--max-drift 0.05` will block most edits.
- Add `--max-format-changes 0` when a parser consumes the output.
- Use `--max-flips 0` when you want check flips to fail even if you have
  raised `--max-regressions` to tolerate a judge that is too eager.
