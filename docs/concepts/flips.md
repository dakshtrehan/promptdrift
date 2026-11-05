# Checks and flips

Deterministic checks are the most trustworthy regression signal promptdrift
has. They need no model, cost nothing, and a failure is a fact rather than a
score.

## Three check types

A check runs on both outputs of a case. A case **passes** a side when every
check passes on that side.

| Type | Passes when | Options |
|---|---|---|
| `regex` | `re.search(pattern, output)` finds a match | `flags`: any of `i` (ignore case), `m` (multiline), `s` (dot matches newline) |
| `json_keys` | the output parses as a JSON object and has every listed top-level key | one surrounding code fence is stripped first |
| `exact` | the output equals `value` after trimming whitespace | `strip: false` keeps whitespace, `ignore_case: true` folds case; with no `value` (or `"exact": true`) the case's `expected` field is used |

Checks are declared per case, as a list or as a shorthand dict:

=== "List form"

    ```json
    {"case_id": "extract", "input": "...", "checks": [
      {"type": "regex", "pattern": "\\bAda\\b", "flags": "i"},
      {"type": "json_keys", "keys": ["name", "age"]},
      {"type": "exact", "value": "96", "ignore_case": true}
    ]}
    ```

=== "Shorthand"

    ```json
    {"case_id": "extract", "input": "...", "expected": "96",
     "checks": {"regex": "\\b96\\b", "json_keys": ["name"], "exact": true}}
    ```

=== "Default spec file"

    `checks.yaml`, applied to every case that declares none:

    ```yaml
    - type: regex
      pattern: "^(?![\\s\\S]*(\\[[A-Za-z ]*[Nn]ame\\]|\\{\\{[^}]*\\}\\}))"
    ```

    ```bash
    promptdrift diff outputs.jsonl --checks checks.yaml
    ```

Specs are validated before anything runs: an unknown type, a missing field
or a regex that does not compile is a `ValueError`, not a silent pass.

!!! note "`expected` alone is not a check"
    A case with an `expected` value but no checks is scored exactly as in
    v0.1.1: `expected` is context for the judge, nothing more. Add
    `"checks": {"exact": true}` to turn it into a hard comparison.

## Flips

What matters is not whether a check passes, but whether its result **changed**
between the versions:

| `checks_passed_a` | `checks_passed_b` | `check_flip` | Effect |
|---|---|---|---|
| pass | fail | `pass_to_fail` | The case is a **regression**: `drift_level` becomes `regression` and the case counts in `report.regressions` whatever its similarity |
| fail | pass | `fail_to_pass` | The case is an **improvement**, unless something else (the judge) made it a regression |
| pass | pass | `None` | Nothing to report |
| fail | fail | `None` | Both sides fail; the check never passed, so this is not a regression of this change |

A `pass_to_fail` flip is the strongest signal the tool has. In the offline
example, `refund_window` has only 6 percent TF-IDF drift (two digits changed
in a long sentence), yet it is a regression because the regex `\b30 days\b`
stopped matching.

The summary section `checks` reports:

```json
"checks": {"cases_with_checks": 16, "pass_rate_a": 0.9375, "pass_rate_b": 0.875,
           "pass_to_fail": 2, "fail_to_pass": 1}
```

It is `null` when no case has checks, and without checks every number in the
report is exactly what it was before checks existed.

## Checks with several samples

When a case has multiple samples per side (see [Noise floor](noise-floor.md)),
checks run on every sample. A side passes when **at least half** of its
samples pass, and `check_pass_rate_a` / `check_pass_rate_b` record the exact
share. In the offline example, one of three v2 samples of `refund_subject_line`
leaks a `[Customer Name]` placeholder: the pass rate on B is 0.67, the case
still passes, and the leak is visible without flipping the verdict.

## Gating on flips

- `--max-flips N` on the CLI, `max-flips` on the Action and `max_flips=` in
  `assert_no_regressions()` cap the number of `pass_to_fail` flips (inclusive:
  `--max-flips 0` fails on the first).
- Since a flip is also a regression, `--fail-on-regression`,
  `--max-regressions 0` and the plugin's per-case items catch it too.
- Per case the reason is spelled out everywhere: the terminal shows
  `checks  pass to fail (regex)`, the JUnit failure message is
  `check pass_to_fail (regex)`, and the pytest item prints the same line.
