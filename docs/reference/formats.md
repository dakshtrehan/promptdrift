# File formats

Every file promptdrift reads or writes, with its schema.

## Prompt config (live mode)

YAML or JSON, flat, one per prompt version. Loaded by `load_config()`.

```yaml
version: v1                  # required, used in report headers
provider: openai             # required: openai | anthropic | ollama
model: gpt-4o-mini           # required
prompt_template: "{input}"   # required; {input} is replaced with each case's input

system: ""                   # optional system prompt (omitted from the request when empty)
temperature: 0.0             # optional, default 0.0 (dropped for models that reject it)
max_tokens: 1024             # optional, default 1024
embedding_model: "..."       # optional; setting it turns on embedding similarity
provider_options: {}         # optional extra keyword arguments for the provider constructor
```

| Field | Required | Notes |
|---|---|---|
| `version` | yes | any string; it labels side A or B everywhere |
| `provider` | yes | `openai`, `anthropic` or `ollama` |
| `model` | yes | passed to the provider as given |
| `prompt_template` | yes | `{input}` is substituted with `str.format`; templates with other literal braces fall back to plain replacement, so JSON examples in a prompt are fine |
| `system` | no | default `""` |
| `temperature` | no | default `0.0`. Sent to OpenAI as is; to Anthropic only for Claude models before 4.7; to Ollama as `options.temperature` |
| `max_tokens` | no | default `1024`. Ollama receives it as `options.num_predict` |
| `embedding_model` | no | when set, similarity switches to embeddings with this model (OpenAI, or Ollama for Ollama configs) |
| `provider_options` | no | a mapping forwarded to the provider constructor, for example `base_url` for Ollama or `temperature_supported: true|false` to override Anthropic sampling detection |

!!! note "Dicts passed to `compare()`"
    `compare()` accepts config dicts as well as paths. Since 0.3.0 a dict
    gets the same defaults as `load_config()` for `system`, `temperature` and
    `max_tokens`, so those keys can be left out.

## Test cases (live mode)

JSON array, JSONL or YAML list. Loaded by `load_cases()`. Each case needs
`input`; `id` is assigned as `case_N` when missing.

```json
[
  {"id": "factual_01", "input": "What is the capital of France?", "expected": "Paris",
   "checks": {"regex": "Paris"}, "tags": ["factual"]},
  {"input": "What is 15% of 200?"}
]
```

| Field | Required | Notes |
|---|---|---|
| `input` | yes | substituted into `prompt_template` |
| `id` | no | the case name in reports; default `case_1`, `case_2`, ... |
| `expected` | no | judge context, and the default value for an `exact` check |
| `checks` | no | a checks spec (below) |
| `tags` | no | a list of strings, or one string |

## Outputs (offline mode)

JSONL (one object per line) or a JSON array. Loaded by `load_outputs()`.

```json
{"case_id": "capital", "input": "What is the capital of France?", "expected": "Paris", "output_a": "Paris", "output_b": "The capital of France is Paris.", "checks": {"regex": "Paris"}, "tags": ["factual"]}
{"case_id": "reverse", "output_a": ["s[::-1]", "s[::-1]", "''.join(reversed(s))"], "output_b": ["s[::-1]", "''.join(reversed(s))", "s[::-1]"], "tags": ["code"]}
```

| Field | Required | Notes |
|---|---|---|
| `case_id` | yes | `id` is accepted as an alias |
| `output_a`, `output_b` | yes | a string, or a list of strings (samples). `null` reads as `""`; any other type is an error naming the record |
| `input` | no | shown in reports and given to the judge; default `""` |
| `expected` | no | as for cases |
| `checks` | no | as for cases |
| `tags` | no | as for cases |

## Checks spec

Used inline under `checks` on a case or record, or as a whole file passed
with `--checks FILE` (JSON or YAML) and applied to every case that declares
none. Validated up front; a bad spec is a `ValueError`.

=== "List form"

    ```yaml
    - type: regex
      pattern: "\\bAda\\b"
      flags: i                # any of i, m, s
    - type: json_keys
      keys: [name, age]
    - type: exact
      value: "96"
      ignore_case: true       # optional
      strip: true             # optional, default true
    ```

=== "Shorthand dict"

    ```json
    {"regex": "\\b96\\b", "json_keys": ["name"], "exact": "96"}
    ```

    `"exact": true` means "compare with the case's `expected`".

=== "Single check"

    A dict with a `type` key is treated as a one-item list:

    ```json
    {"type": "regex", "pattern": "^\\{"}
    ```

| Type | Fields | Passes when |
|---|---|---|
| `regex` | `pattern` (required), `flags` | `re.search` matches |
| `json_keys` | `keys` (non-empty list, or one string) | the output, after one surrounding code fence is removed, parses as a JSON object containing every key |
| `exact` | `value` (defaults to `expected`), `strip`, `ignore_case` | the output equals `value` |

## JSON report (`--output`, `--json`, `report.to_dict()`)

```json
{
  "version_a": "v1", "version_b": "v2",
  "model_a": "stored", "model_b": "stored",
  "total_cases": 16,
  "mode": "offline",
  "similarity": "tfidf",
  "summary": {
    "avg_drift": 0.179, "regression_rate": 0.125,
    "regressions": 2, "improvements": 1, "neutral": 13,
    "structural": {"format_changes": 3, "format_change_rate": 0.1875,
                   "json_validity_changes": 1, "json_broken": 1, "median_length_ratio": 1.21},
    "checks": {"cases_with_checks": 16, "pass_rate_a": 0.9375, "pass_rate_b": 0.875,
               "pass_to_fail": 2, "fail_to_pass": 1},
    "judge": null,
    "stability": {"cases": 1, "samples_per_side": 3, "avg_within_drift_a": 0.645,
                  "avg_within_drift_b": 0.766, "avg_noise_floor": 0.706,
                  "avg_excess_drift": -0.127, "cases_above_noise": 0},
    "ci": {"level": 0.95, "n_bootstrap": 1000, "avg_drift": [0.078, 0.304],
           "regression_rate": [0.0, 0.3125], "avg_excess_drift": [-0.127, -0.127]}
  },
  "cases": [ {"case_id": "refund_window", "...": "every CaseResult field"} ]
}
```

- `mode` is `live` or `offline`; `similarity` is the backend that was
  requested (`tfidf` or `embedding`).
- Sections that do not apply are `null`: `checks` when no case has checks,
  `judge` when nothing was judged, `stability` when no case has several
  samples, `ci` when the bootstrap is off.
- `cases` holds every field of `CaseResult` in declaration order, including
  `samples_a` / `samples_b`, the check results, the judge verdicts in both
  orders and `tags`. See [`promptdrift.core`](api.md#promptdrift.core).
- `evaluate_thresholds()`, `render_markdown()` and `report_from_dict()`
  accept this dict, so a saved report can be gated or rendered later.

## HTML report (`--html`)

One file, inline CSS and vanilla JavaScript, no external requests (the only
URL in it is the link to the repository). Verdict header, summary cards,
confidence interval bars, a per-tag table, and a case table that sorts by
any column, filters by free text, level, judge verdict, tag and "regressions
only", and expands each row into a side-by-side word-level diff plus the
check results, judge reasoning, structure and samples. Light and dark mode
follow the OS; the toggle is remembered in `localStorage`. With JavaScript
disabled every detail row is visible and the controls are hidden. All text
goes through `html.escape`, so an output containing `<script>` is shown, not
run.

## CSV (`--csv`, `to_csv()`)

One row per case, `\r\n` line endings, booleans as `true` / `false`, missing
values empty, tags joined with `;`.

```text
case_id,tags,similarity,drift_score,drift_level,judge_verdict,check_flip,format_changed,signature_a,signature_b,json_valid_a,json_valid_b,length_ratio,n_samples,noise_floor,excess_drift,latency_a,latency_b,tokens_a,tokens_b,similarity_method
percent,math,0.0,1.0,regression,,pass_to_fail,false,lines:1,lines:1,false,false,8.0,1,,,0.0,0.0,0,0,tfidf
```

`save_csv(report, path, include_outputs=True)` appends `input`, `output_a`
and `output_b` (multi-line, so off by default).

## JUnit XML (`--junit`, `to_junit()`)

`<testsuites>` wrapping one `<testsuite name="promptdrift">`. One
`<testcase>` per case, `name` is the case id and `classname` the first tag
(or `"<version_a> vs <version_b>"`). A regression is a `<failure>` whose
message names the reason (`check pass_to_fail (regex)`, `judge regressed`)
and whose body shows the input and both outputs; a judge `error` on a
non-regressed case is an `<error>`. `time` is the sum of stored latencies.

Suite `<properties>`: `promptdrift_version`, `version_a`, `version_b`,
`model_a`, `model_b`, `mode`, `similarity`, `avg_drift`, `regression_rate`,
`regressions`, `improvements`, `neutral`, `format_changes`, `verdict`,
`verdict_status`, `verdict_reason`, and when the bootstrap is on `ci_level`,
`avg_drift_ci_low`, `avg_drift_ci_high`, `regression_rate_ci_low`,
`regression_rate_ci_high`, `avg_excess_drift_ci_low`, `avg_excess_drift_ci_high`.

```xml
<testsuites name="promptdrift" tests="5" failures="1" errors="0" time="0.000">
  <testsuite name="promptdrift" tests="5" failures="1" errors="0" skipped="0" time="0.000" hostname="promptdrift" package="promptdrift">
    <properties>
      <property name="promptdrift_version" value="0.3.0" />
      <property name="verdict" value="DO NOT SHIP" />
      <property name="avg_drift_ci_low" value="0.2141" />
    </properties>
    <testcase name="capital" classname="factual" time="0.000" />
    <testcase name="percent" classname="math" time="0.000">
      <failure message="check pass_to_fail (regex)" type="regression">check pass_to_fail (regex)

input: What is 15% of 200?

output A:
30

output B:
15% of 200 is 3.
</failure>
    </testcase>
  </testsuite>
</testsuites>
```

(Excerpt; the real file is a single line and carries every property.)

## Markdown (`render_markdown()`)

GitHub flavoured Markdown for pull request comments and `GITHUB_STEP_SUMMARY`:
an optional hidden marker line, a heading, the verdict line with an emoji,
threshold failures, the summary table with confidence intervals, the per-tag
table, a collapsible `<details>` block with regressions and notable cases
(truncated A and B outputs in code spans), and a footer with the version and
a link to the artifact. Text is escaped and code spans use a fence one
backtick longer than the longest run inside, so model outputs cannot break
the layout. See the [Python API](api.md#promptdrift.markdown).
