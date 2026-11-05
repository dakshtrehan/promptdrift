# Structural drift

Semantic similarity can stay high while the shape of the output changes in a
way that breaks whatever parses it: JSON gains a markdown fence, a numbered
list turns into a paragraph, a reply doubles in length. Structural drift
measures the shape, with no model call.

## Format signatures

Each output gets a signature built from five observations:

| Part | Detected as |
|---|---|
| `fence` | the whole output is wrapped in one markdown code fence |
| `json:object` / `json:array` | the output (after removing one surrounding fence) parses as a JSON object or array; bare scalars such as `30` count as text |
| `list` | lines start with `-`, `*`, `+`, a bullet or `1.` / `1)` |
| `headings` | markdown headings (`#` to `######`) |
| `lines:N` | the non-blank line count, bucketed as `1`, `2-4` or `5+` |

The comparable string form joins the parts with `+`, for example
`fence+json:object+lines:1` or `list+lines:2-4`. An empty output is `empty`.
The line count is bucketed on purpose: one extra sentence is not a format
change, prose turning into a five-line list is. The exact count is still
available from `format_signature()` if you need it.

Per case the report records:

| Field | Meaning |
|---|---|
| `signature_a`, `signature_b` | the two signature strings |
| `format_changed` | `signature_a != signature_b` |
| `json_valid_a`, `json_valid_b` | does each side parse as JSON after fence removal |
| `json_validity_changed` | validity differs between the sides |
| `length_ratio` | `len(B) / len(A)` in characters after trimming; `None` when A is empty and B is not, `1.0` when both are empty |

## JSON validity

Validity is judged **after removing one surrounding code fence**. That is what
most production parsers that strip fences see, so:

- "the JSON gained a fence" is a format change (`json:object+lines:1` to
  `fence+json:object+lines:1`) but not a validity change,
- "the JSON lost its closing brace" is both a format change and a validity
  change, and counts as **JSON broken** (valid in A, invalid in B).

The offline example has one of each: `invoice_json_fenced` and
`order_status_json`.

## Summary

```json
"structural": {"format_changes": 3, "format_change_rate": 0.1875,
               "json_validity_changes": 1, "json_broken": 1,
               "median_length_ratio": 1.21}
```

The length ratio is summarised as a **median**, not a mean: ratios are skewed
and one reply that grew tenfold would dominate a mean.

## Reported, not a regression

A format change is reported and turns the verdict into REVIEW, but it does not
count as a regression on its own, because a format change can be exactly what
you intended (you asked for a list). When a format must not change, make it a
hard rule:

- a `json_keys` [check](flips.md) fails when the output stops being a JSON
  object with the keys you need, and a pass-to-fail flip is a regression,
- `--max-format-changes N` (CLI), `max-format-changes` (Action) and
  `max_format_changes=` (`assert_no_regressions`) cap the number of cases whose
  signature changed,
- a `regex` check such as `^\{` or `^- ` pins a shape without a JSON parse.

## Python

```python
from promptdrift.structure import format_signature, signature_string, structural_diff

signature_string("```json\n{\"a\": 1}\n```")     # 'fence+json:object+lines:1'
structural_diff("1. Boil water\n2. Add the egg", "Boil water, then add the egg.")
# {'signature_a': 'list+lines:2-4', 'signature_b': 'lines:1', 'format_changed': True, ...}
```

See the [API reference](../reference/api.md#promptdrift.structure) for every helper.
