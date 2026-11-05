# Recipe: offline logs

The cheapest comparison is one you do not have to generate. If your pipeline
already logs model outputs per request, you can diff two deployments, two
days or two prompt versions for free, as often as you like, with no key.

## The record format

One JSON object per line (JSONL), or a JSON array of the same objects:

```json
{"case_id": "refund_window", "input": "How long do I have to request a refund?", "expected": "30 days", "output_a": "You can request a full refund within 30 days of purchase.", "output_b": "You can request a full refund within 14 days of purchase.", "tags": ["billing", "refund"], "checks": {"regex": "\\b30 days\\b"}}
```

| Field | Required | Meaning |
|---|---|---|
| `case_id` | yes (`id` is accepted too) | the case name in every report; pair the two sides by it |
| `output_a` | yes | the baseline output: a string, or a list of strings for several samples |
| `output_b` | yes | the candidate output, same shape rules |
| `input` | no | shown in the report and given to the judge |
| `expected` | no | judge context, and the value an `exact` check with no `value` compares against |
| `checks` | no | deterministic checks for this record (list or shorthand dict), see [Checks and flips](../concepts/flips.md) |
| `tags` | no | a list of labels (a single string is accepted) for per-tag breakdowns, see [Tags](tags.md) |

Rules:

- a `null` output is read as the empty string (a model can return nothing);
  any other non-string, non-list value is an error that names the record number,
- `output_a` and `output_b` may have different numbers of samples,
- records without `checks` get the default spec from `--checks FILE`
  (`checks=` in Python) when one is given,
- unknown fields are ignored, so you can keep your own metadata in the file.

## Samples lists

To measure the [noise floor](../concepts/noise-floor.md), log several
outputs per case and side:

```json
{"case_id": "refund_subject_line", "input": "Write a subject line for a refund confirmation email.",
 "output_a": ["Your refund has been processed", "Refund confirmation", "Your refund is on its way"],
 "output_b": ["Good news: your refund is complete", "Refund confirmation for your order", "Refund processed for [Customer Name]"],
 "tags": ["refund", "tone"]}
```

Structure, the judge and the `output_a` / `output_b` fields of the result use
the first sample; drift uses every pair; checks run on every sample and a side
passes when at least half pass.

## Exporting from a pipeline

Whatever your logging looks like, the conversion is a group-by on the case
id. A minimal example from two JSONL logs with `{"id", "prompt", "completion"}`
records:

```python
import json

def load(path):
    with open(path, encoding="utf-8") as f:
        return {r["id"]: r for r in map(json.loads, f) if r.get("completion") is not None}

a, b = load("logs/2026-11-01.jsonl"), load("logs/2026-11-04.jsonl")
with open("outputs.jsonl", "w", encoding="utf-8") as out:
    for case_id in sorted(a.keys() & b.keys()):
        out.write(json.dumps({
            "case_id": case_id,
            "input": a[case_id]["prompt"],
            "output_a": a[case_id]["completion"],
            "output_b": b[case_id]["completion"],
        }, ensure_ascii=False) + "\n")
```

For several samples per side, collect lists instead of single strings. If the
logs are in a warehouse, the same thing is a self-join on the case id with
`array_agg` on the completion.

From a live `promptdrift compare ... --output report.json` run, each case in
`report.json` has `output_a`, `output_b`, `samples_a` and `samples_b`, so a
previous live report can also become the baseline of an offline diff:

```python
import json

report = json.load(open("report.json"))
with open("baseline.jsonl", "w", encoding="utf-8") as out:
    for c in report["cases"]:
        out.write(json.dumps({"case_id": c["case_id"], "input": c["input"],
                              "output_a": c["samples_b"] or c["output_b"],   # yesterday's B is today's A
                              "output_b": None,                                 # fill from the next run
                              "tags": c["tags"]}) + "\n")
```

## Running the diff

```bash
# docs-test: run
promptdrift diff examples/offline/outputs.jsonl --name-a v1 --name-b v2 --checks examples/offline/checks.yaml --html report.html --junit junit.xml
```

`--name-a` and `--name-b` label the sides (default `A` and `B`). The judge is
off; add `--judge` to opt in (that calls a model). In Python:

```python
from promptdrift import compare_outputs, load_outputs

records = load_outputs("outputs.jsonl")          # validated list of dicts
report = compare_outputs(records, version_a="2026-11-01", version_b="2026-11-04",
                         checks=[{"type": "regex", "pattern": "^(?![\\s\\S]*\\[Customer Name\\])"}])
```

`compare_outputs` also accepts a path directly, or a list of plain dicts built
in code, as `examples/python_api.py` shows:

```bash
# docs-test: run
python examples/python_api.py
```

## The example scenario

`examples/offline/` is a complete scenario: 16 logged replies of a support
assistant before and after a prompt edit, with tags, per-record checks, a
default check spec that catches unfilled placeholders, and a multi-sample
record. Its README has a table of what each case demonstrates. `run.sh`
produces every report format and exits 1 on purpose because the data holds
two regressions:

```bash
bash examples/offline/run.sh
```
