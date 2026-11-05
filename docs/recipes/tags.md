# Recipe: tags

Tags label cases so the reports can break drift, regressions, flips and
format changes down by area. A warmer prompt that moves every `tone` case
and no `structured` case is working as designed; the same drift spread
evenly over `billing` is a different story.

## Declaring tags

Add a `tags` list to any case in a cases file or any record in an outputs
file. A single string is accepted and normalised to a one-item list.

=== "Cases (live mode)"

    ```json
    [
      {"id": "refund_window", "input": "How long do I have to request a refund?",
       "checks": {"regex": "\\b30 days\\b"}, "tags": ["billing", "refund"]},
      {"id": "order_status_json", "input": "Return order A1001 as JSON.",
       "checks": {"json_keys": ["order_id", "status"]}, "tags": ["orders", "structured"]},
      {"id": "greeting", "input": "Hi!", "tags": "smalltalk"}
    ]
    ```

=== "Outputs (offline mode)"

    ```json
    {"case_id": "refund_window", "output_a": "...", "output_b": "...", "tags": ["billing", "refund"]}
    ```

=== "YAML cases"

    ```yaml
    - id: refund_window
      input: How long do I have to request a refund?
      tags: [billing, refund]
      checks:
        regex: "\\b30 days\\b"
    ```

Tags must be a list of strings (or one string); anything else is rejected
when the file is loaded. A case may carry any number of tags and counts once
under each.

## Where tags appear

| Output | How |
|---|---|
| `CaseResult.tags` and `report.json` | the list as given (`null` when absent) |
| HTML report | chips next to the case id, a **By tag** table (cases, average drift, regressions, check flips, format changes) and a tag filter on the case table |
| Markdown PR comment and job summary | the same **By tag** table when any case carries tags |
| CSV | a `tags` column joined with `;` |
| JUnit XML | the first tag is the testcase `classname`; cases without tags use `"<version_a> vs <version_b>"` |
| Terminal report | not shown; the terminal keeps its v0.2.0 layout |

The per-tag table from the Markdown renderer for the five-record example:

```text
| Tag | Cases | Avg drift | Regressions | Check flips | Format changes |
| --- | ---: | ---: | ---: | ---: | ---: |
| code | 1 | 26.3% | 0 | 0 | 0 |
| factual | 1 | 59.2% | 0 | 0 | 0 |
| howto | 1 | 33.3% | 0 | 0 | 1 |
| math | 1 | 100.0% | 1 | 1 | 0 |
| structured | 1 | 10.6% | 0 | 0 | 1 |
```

## Gating per tag

The CLI thresholds are global. For a per-tag gate, filter the cases in
Python; everything you need is on the report:

```python
from promptdrift import compare_outputs

report = compare_outputs("outputs.jsonl", version_a="v1", version_b="v2")

def by_tag(tag):
    return [c for c in report.cases if c.tags and tag in c.tags]

billing = by_tag("billing")
regressed = [c.case_id for c in billing if c in report.regressions]
assert not regressed, f"billing regressions: {regressed}"

structured = by_tag("structured")
assert not any(c.format_changed for c in structured), "a structured case changed shape"
```

With the [pytest plugin](pytest.md), `-k` selects items by case id, not by
tag; use the `promptdrift_report` fixture for tag-level assertions as above.

## Naming

Short lowercase words work best in the chips and the tables:
`billing`, `refund`, `orders`, `structured`, `tone`, `sla`, `howto`. The
offline example uses thirteen such tags across sixteen cases, which is enough
to see, for instance, that both regressions sit in `billing`/`refund` and
`orders`/`structured`, and that `tone` moved without regressing.
