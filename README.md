<div align="center">

# promptdrift ⚡

**Semantic, structural and check-based diff for LLM prompts and model outputs.**
Catch regressions before they reach production.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/dakshtrehan/promptdrift/actions/workflows/ci.yml/badge.svg)](https://github.com/dakshtrehan/promptdrift/actions)

</div>

---

You tweaked your system prompt. Or swapped one model for another. Or bumped the temperature.

**How do you know your outputs didn't quietly get worse?**

`promptdrift` compares two versions of a prompt (or two sets of stored outputs) case by case and tells you:

* how much the meaning changed (semantic drift),
* whether the shape changed in a way that could break a parser (structural drift),
* whether hard checks you care about went from pass to fail (deterministic checks),
* how much of the change is just sampling noise (multi-sample stability),
* how sure you can be about the headline numbers (bootstrap confidence intervals),
* and, optionally, what an LLM judge thinks, asked in both orders so position bias is visible.

```text
$ promptdrift diff examples/outputs.jsonl --name-a v1 --name-b v2 --summary

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚡ promptdrift  v0.2.0 · github.com/dakshtrehan/promptdrift
  offline mode: stored outputs, no generation calls
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  A  v1  (stored)
  B  v2  (stored)
  Cases: 5

  Summary
  ──────────────────────────────────────────────────────────────────────
  Avg drift         ██████████░░░░░░░░░░░░  45.9%  95% CI 21.4 to 74.0%
  Regression rate   ████░░░░░░░░░░░░░░░░░░  20.0%  95% CI 0.0 to 60.0%

  ↑ Improved    0   ↓ Regressed   1   → Neutral     4

  Structure  format changed 2   JSON validity changed 0 (broken 0)   median length B/A 1.46x

  Checks     pass A 100.0%  B 66.7%  (3 cases)   pass→fail 1   fail→pass 0

  Stability  n=3/side   noise floor 39.5% (A 39.5%, B 39.5%)   excess drift -13.2%   above noise 0/1

  ──────────────────────────────────────────────────────────────────────
  ✗  DO NOT SHIP: 1 regression(s) detected
  ──────────────────────────────────────────────────────────────────────
```

That run made no model calls and cost nothing. With `--fail-on-regression` the exit code is `1` when a regression is found, so it drops straight into CI.

---

## Install

> promptdrift is **not on PyPI yet**. `pip install promptdrift` will not work until the first release is published. Install from GitHub for now.

```bash
pip install "git+https://github.com/dakshtrehan/promptdrift.git"
```

Or from a clone:

```bash
git clone https://github.com/dakshtrehan/promptdrift
cd promptdrift
pip install -e .
```

The only required dependency is PyYAML. Provider SDKs are optional extras, needed only for live mode or the judge:

```bash
pip install -e ".[openai]"      # OpenAI
pip install -e ".[anthropic]"   # Anthropic / Claude
pip install -e ".[all]"         # both
```

Ollama (local models) needs `requests`, which most environments already have.

---

## Two ways to run it

### 1. Offline mode: compare outputs you already have

No model is called. Give it JSONL with one record per case:

```jsonl
{"case_id": "capital", "input": "What is the capital of France?", "output_a": "Paris", "output_b": "The capital of France is Paris."}
{"case_id": "extract", "output_a": "{\"name\": \"Ada\"}", "output_b": "```json\n{\"name\": \"Ada\"}\n```"}
```

| Field | Required | Meaning |
|---|---|---|
| `case_id` | yes (`id` also accepted) | Case name in the report |
| `output_a`, `output_b` | yes | A string, or a list of strings for multi-sample stability |
| `input` | no | Shown in the report and given to the judge |
| `expected` | no | Judge context, and the default value for an `exact` check |
| `checks` | no | Deterministic checks, see below |

A JSON array of the same records also works.

```bash
promptdrift diff outputs.jsonl --name-a old --name-b new
promptdrift diff outputs.jsonl --checks checks.json --fail-on-regression
promptdrift diff outputs.jsonl --judge           # opt in to an LLM judge (this does call a model)
```

```python
from promptdrift import compare_outputs, print_report

report = compare_outputs("outputs.jsonl", version_a="old", version_b="new")
print_report(report)

# or pass records directly
report = compare_outputs([
    {"case_id": "c1", "output_a": "Paris", "output_b": "The capital is Paris."},
])
```

### 2. Live mode: run two prompt configs against test cases

```bash
promptdrift init --dir ./prompts         # scaffolds v1.yaml, v2.yaml, golden.json
export OPENAI_API_KEY=sk-...             # or ANTHROPIC_API_KEY, or use Ollama
promptdrift compare prompts/v1.yaml prompts/v2.yaml --cases prompts/golden.json
```

```yaml
# v1.yaml
version: v1
provider: openai            # openai | anthropic | ollama
model: gpt-4o-mini
system: "You are a concise assistant."
prompt_template: "{input}"
temperature: 0.0
max_tokens: 256
```

```python
from promptdrift import compare, print_report

report = compare("v1.yaml", "v2.yaml", cases="golden.json", samples=3)
print_report(report)
```

Test cases can be JSON, JSONL or YAML. Each needs `input`; `id`, `expected` and `checks` are optional.

---

## What gets measured

### Semantic drift

Per case, `similarity` is in 0 to 1 and `drift_score = 1 - similarity`, bucketed as none (< 5%), minor (< 20%), moderate (< 50%) or major.

The default similarity is **TF-IDF cosine**: pure Python, free, offline and deterministic. It measures word overlap, not deep meaning, so treat it as a cheap first signal. Paraphrases with different words score as drift; that is what the judge and checks are for.

Embedding similarity is opt-in:

```bash
promptdrift compare v1.yaml v2.yaml --cases golden.json --similarity embedding
```

or set `embedding_model` in a config. It uses OpenAI embeddings (or Ollama for Ollama configs). If no embedder is reachable the run falls back to TF-IDF and says so. Each case records the method that actually ran in `similarity_method`. Embedding and TF-IDF scores are on different scales, so do not compare numbers across the two.

### Structural drift

Did the shape of the output change? Each output gets a format signature built from:

* markdown code fence,
* JSON container type (`object` or `array`; bare scalars like `30` count as text),
* bullet or numbered list,
* markdown headings,
* non-blank line count (bucketed as 1, 2 to 4, 5 or more).

Per case you get `signature_a`, `signature_b`, `format_changed`, `json_valid_a`, `json_valid_b`, `json_validity_changed` and `length_ratio` (`len(B) / len(A)`). The summary counts format changes, JSON validity changes, JSON that broke (valid in A, invalid in B) and the median length ratio. JSON validity is judged after removing one surrounding code fence, so "the JSON gained a fence" is a format change but not a validity change.

Structural changes are reported, not counted as regressions on their own. Use a check if a format change should fail the run.

### Deterministic checks

Checks run on both outputs without any model. A case passes when all its checks pass. **A pass in A that becomes a fail in B is a regression.**

```json
{"id": "extract", "input": "...", "checks": [
  {"type": "regex", "pattern": "\\bAda\\b", "flags": "i"},
  {"type": "json_keys", "keys": ["name", "age"]},
  {"type": "exact", "value": "96", "ignore_case": true}
]}
```

Shorthand: `"checks": {"regex": "\\b96\\b", "json_keys": ["name"], "exact": true}`. An `exact` check with no value (or `"exact": true`) compares against the case's `expected`.

| Type | Passes when |
|---|---|
| `regex` | `re.search(pattern, output)` matches. Optional `flags`: any of `i`, `m`, `s` |
| `json_keys` | Output parses as a JSON object (one code fence allowed) with every listed top-level key |
| `exact` | Output equals `value` after trimming whitespace (`strip: false` to disable; `ignore_case: true` optional) |

Apply one spec to every case that has none with `--checks checks.json` (JSON or YAML) or `checks=` in Python. The summary reports pass rates for A and B and the `pass_to_fail` and `fail_to_pass` counts.

### Multi-sample stability

Outputs vary between runs of the same prompt. Without a noise floor you cannot tell a real change from sampling noise.

```bash
promptdrift compare v1.yaml v2.yaml --cases golden.json --samples 5
```

In offline mode, pass lists: `"output_a": ["...", "...", "..."]`.

Per case:

* `drift_score`: mean drift over all A x B sample pairs,
* `within_drift_a`, `within_drift_b`: mean drift between samples of the same version,
* `noise_floor`: mean of the two within-version drifts,
* `excess_drift = drift_score - noise_floor`: how much more the versions differ than each one differs from itself. It can be negative, and is left unclipped so averages are not biased upward.

The summary reports the average noise floor, average excess drift and how many cases are more than 0.05 above their noise floor. Checks run on every sample; a side passes when at least half its samples pass. Structure and the judge use the first sample of each side.

### Confidence intervals

The report shows 95 percent bootstrap confidence intervals for the average drift and the regression rate (and the average excess drift when samples are used). Cases are resampled 1000 times with a fixed seed, so the same input always gives the same interval. Change it with `--bootstrap N` and `--seed S`, or turn it off with `--bootstrap 0`.

With only a handful of cases the intervals are wide, as in the example above, and the percentile bootstrap tends to be a little too narrow for very small samples. Treat a wide interval as a reason to add cases, not as noise to ignore.

### LLM judge

In live mode an LLM judge rates each drifted case as improved, regressed or neutral. In offline mode it is off unless you pass `--judge`.

* **Provider-agnostic.** `--judge-provider openai|anthropic|ollama`, or in Python pass any object with a `complete(system, prompt, **kwargs)` method as `judge_provider`.
* **Default provider** comes from the environment: OpenAI if `OPENAI_API_KEY` is set, otherwise Anthropic if only `ANTHROPIC_API_KEY` is set. With neither, the judge is skipped with a message. Default models: `gpt-4o-mini` and `claude-haiku-4-5-20251001`; override with `--judge-model`.
* **Both orders.** Each case is judged as (A, B) and again as (B, A). If the two verdicts agree, that is the verdict. If they disagree, the case is marked `inconsistent`; if a call fails or returns unusable text, it is marked `error`. Neither counts as a regression or an improvement. The summary reports the order-inconsistency rate, which is a direct measure of the judge's position bias. `--judge-single-order` restores the single-call behaviour of v0.1.1 at half the cost.

The judge is the only feature that costs money in offline mode, and it is opt-in there.

---

## Providers

| Provider | `provider:` | Notes |
|---|---|---|
| OpenAI | `openai` | Needs `OPENAI_API_KEY` and `pip install openai` |
| Anthropic | `anthropic` | Needs `ANTHROPIC_API_KEY` and `pip install anthropic` (SDK 1.x or 2.x) |
| Ollama | `ollama` | Local server at `http://localhost:11434` |

**Anthropic sampling parameters.** Claude models from 4.7 on only accept default sampling, and `anthropic` SDK 2.x defaults to an API version that rejects `temperature`. promptdrift sends `temperature` (and `top_p`, `top_k`) only to models before 4.7, and then with the `anthropic-version: 2023-06-01` header. For newer models the value from your config is dropped. Response text is read from `text` blocks only, so thinking blocks are skipped. If you use a model id promptdrift cannot parse, set the behaviour explicitly:

```yaml
provider: anthropic
model: my-proxy-model
provider_options:
  temperature_supported: true
```

`provider_options` passes extra keyword arguments to any provider, for example `base_url` for Ollama.

---

## Config reference

```yaml
version: v1                  # required, used in report headers
provider: openai             # required: openai | anthropic | ollama
model: gpt-4o-mini           # required
prompt_template: "{input}"   # required, {input} is replaced with each test case

system: ""                   # optional system prompt
temperature: 0.0             # optional, default 0.0 (ignored by models that reject it)
max_tokens: 1024             # optional, default 1024
embedding_model: "..."       # optional; setting it turns on embedding similarity
provider_options: {}         # optional extra provider arguments
```

Templates with other literal braces (for example a JSON example) are fine: only `{input}` is substituted.

## CLI reference

```text
promptdrift compare A.yaml B.yaml --cases FILE [options]
promptdrift diff OUTPUTS.jsonl [--name-a A] [--name-b B] [--judge] [options]
promptdrift init [--dir ./prompts]
promptdrift --version
```

| Option | Applies to | Meaning |
|---|---|---|
| `--no-judge` | compare | Skip the judge |
| `--judge` | diff | Run the judge (calls a model) |
| `--judge-provider`, `--judge-model` | both | Choose the judge |
| `--judge-single-order` | both | Judge A-vs-B only |
| `--similarity tfidf\|embedding` | compare | Similarity backend (default tfidf) |
| `--samples N`, `-n N` | compare | Samples per side per case |
| `--checks FILE` | both | Default checks for cases without their own |
| `--bootstrap N`, `--seed S` | both | Bootstrap resamples (0 disables) and seed |
| `--json`, `-o FILE` | both | Print JSON / save the JSON report |
| `--summary`, `--show-outputs` | both | Less or more per-case detail |
| `--fail-on-regression` | both | Exit 1 if any regression is found |

## JSON report

`report.to_dict()` (or `--json`) contains `version_a`, `version_b`, `model_a`, `model_b`, `total_cases`, `mode` (`live` or `offline`), `similarity`, a `summary` and the per-case results. `summary` holds `avg_drift`, `regression_rate`, `regressions`, `improvements`, `neutral` (all as in v0.1.1) plus `structural`, `checks`, `judge`, `stability` and `ci`. Sections that do not apply are `null`.

A case is a **regression** when the judge says `regressed` or a check goes from pass to fail. It is an **improvement** when the judge says `improved` or a check goes from fail to pass, and it is not a regression.

---

## Python API

Everything from v0.1.1 still works. The main entry points:

```python
from promptdrift import (compare, compare_outputs, load_outputs, load_config, load_cases,
                         print_report, save_report, print_json, DiffReport, CaseResult)

from promptdrift.similarity import tfidf_similarity, text_similarity
from promptdrift.structure import format_signature, signature_string, structural_diff
from promptdrift.checks import normalize_checks, run_checks
from promptdrift.judge import judge_pair, judge_both_orders, resolve_judge
from promptdrift.stats import bootstrap_ci
from promptdrift.scoring import score_case
```

```python
report = compare_outputs("outputs.jsonl")
print(report.regression_rate, report.confidence_intervals()["regression_rate"])
for c in report.regressions:
    print(c.case_id, c.check_flip, c.judge_verdict, c.signature_a, "->", c.signature_b)
```

If you imported the v0.1.1 private helpers `promptdrift.compare._tfidf_similarity` or `promptdrift.compare._judge`, they still work, but switch to `promptdrift.similarity.tfidf_similarity` and `promptdrift.judge.judge_pair`.

---

## Limitations

* TF-IDF similarity is lexical. It will call a faithful paraphrase "drift" and can miss a meaning flip that keeps the same words ("is" vs "is not"). Use checks for facts that must hold, and the judge or embeddings for meaning.
* An LLM judge is itself a model with biases. Running both orders exposes position bias but does not remove other biases. Order-inconsistent cases are excluded from the regression count rather than guessed.
* Bootstrap intervals assume cases are independent and representative. They say nothing about cases you did not include.
* Live mode makes `2 x samples x cases` generation calls, plus up to `2 x cases` judge calls. Offline mode with no judge makes none.

---

## Roadmap

* HTML diff report with side-by-side view
* Reusable GitHub Action wrapper
* Google Gemini provider
* CSV report export
* Per-run cost tracking

See [CHANGELOG.md](CHANGELOG.md) for what shipped.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). The test suite needs no API keys and makes no network calls.

---

## Author

Built by [Daksh Trehan](https://dakshtrehan.com). FDE at [Abacus.AI](https://abacus.ai), 6x Snowflake certified, 50+ articles on Towards Data Science.

> "I built this because I kept breaking my prompts in prod and had no clean way to catch it."

---

<div align="center">

⭐ If promptdrift saves you from a silent regression, give it a star.

</div>
