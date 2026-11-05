# Offline example: a support assistant prompt was edited

A fictional company edited the system prompt of its customer support
assistant (v1 to v2) to sound warmer and to format steps as lists. Before
shipping, the team replayed the same 16 customer messages through both
versions and logged the outputs. `outputs.jsonl` is that log. Nothing here
calls a model, so the example needs no API key and costs nothing.

```bash
# test: offline
promptdrift diff examples/offline/outputs.jsonl --name-a v1 --name-b v2 --checks examples/offline/checks.yaml --summary
```

`run.sh` runs the full pipeline (terminal report, JSON, HTML, CSV and JUnit
XML, plus the CI threshold flags) and exits 1 because the data contains two
deliberate regressions:

```bash
# test: offline, exit 1
bash examples/offline/run.sh
```

`expected_output.txt` is the terminal report from a real run, with colour
codes stripped. Refresh it with `UPDATE_EXPECTED=1 bash examples/offline/run.sh`
after changing the data or the reporter.

## What is in the data

| Case | Tags | What it shows |
|---|---|---|
| `refund_window` | billing, refund | **Regression.** The refund window changed from 30 to 14 days. The `regex` check passes in v1 and fails in v2 (`pass_to_fail`), even though the TF-IDF drift is only 6 percent. |
| `order_status_json` | orders, structured | **JSON broke.** v2 added a friendly preamble and lost the closing brace; `json_keys` goes pass to fail and the structural summary counts one broken JSON. |
| `reset_password_steps` | account, howto | **Format change.** Prose became a bullet list with the same content; reported, not a regression. |
| `cancel_plan_fee` | billing, plan | **Improvement.** v1 forgot to say there is no cancellation fee; the `regex` check goes fail to pass. |
| `greeting`, `ship_to_canada`, `change_email`, `address_change`, `price_currency` | various | Near-identical or identical outputs: zero drift. `price_currency` uses an `exact` check against `expected`. |
| `support_hours`, `warranty_length`, `return_label` | sla, warranty, refund | Small rewordings that keep every required fact; checks pass on both sides. |
| `angry_customer` | tone, escalation | Tone change with the case reference intact (`regex` on `NW-\d{5}`): moderate drift, no regression. |
| `invoice_json_fenced` | billing, structured | JSON gained a code fence. A format change, but still valid JSON with all keys, so the check passes on both sides. |
| `reply_time` | sla | Same meaning, almost no shared words ("24 hours" vs "one business day"): the highest drift in the set, and a reminder that TF-IDF is lexical. |
| `refund_subject_line` | refund, tone | **Multi-sample record**, three logged outputs per side. The within-version drift (noise floor) is larger than the cross-version drift, so the excess drift is negative: the versions differ less than each one differs from itself. One v2 sample leaks a `[Customer Name]` placeholder, which the default check in `checks.yaml` catches as a 67 percent pass rate on side B without flipping the case. |

Every record carries a `tags` list, which the HTML report uses for per-tag
breakdowns. `checks.yaml` is the default check spec for records without their
own `checks`: a regex that fails on unfilled template placeholders.

## Expected summary

```text
Cases: 16    Improved 1   Regressed 2   Neutral 13
Structure    format changed 3   JSON validity changed 1 (broken 1)
Checks       pass A 93.8%  B 87.5%   pass to fail 2   fail to pass 1
Stability    n=3/side   noise floor 70.6%   excess drift -12.7%
```

`tests/test_examples.py` asserts these counts, so the example cannot drift
away from its description without a test failing.
