<div align="center">

# promptdrift ⚡

**Semantic diff for LLM prompts and model outputs.**
Catch regressions before they reach production.

[![PyPI version](https://badge.fury.io/py/promptdrift.svg)](https://pypi.org/project/promptdrift/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/dakshtrehan/promptdrift/actions/workflows/ci.yml/badge.svg)](https://github.com/dakshtrehan/promptdrift/actions)

</div>

---

You tweaked your system prompt. Or swapped GPT-4o for Claude. Or bumped the temperature.

**How do you know your outputs didn't quietly get worse?**

`promptdrift` runs both versions against your test cases and gives you a semantic diff. Not a string comparison, not a BLEU score, but a real answer to *"did these outputs change meaning, and in which direction?"*

```bash
$ promptdrift compare v1.yaml v2.yaml --cases golden.json

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ⚡ promptdrift  v0.1.1
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    A  v1  (gpt-4o-mini)
    B  v2  (gpt-4o-mini)
    Cases: 42

    Summary
    Avg drift         ███████░░░░░░░░░░░░░░░  8.7%
    Regression rate   ██░░░░░░░░░░░░░░░░░░░░  2.4%

    ↑ Improved   2    ↓ Regressed  1    → Neutral   39

    ✗  DO NOT SHIP  1 regression(s) detected
```

Exit code `1` when regressions are detected. Drop it straight into CI.

---

## Install

```bash
pip install promptdrift
```

With your provider:
```bash
pip install "promptdrift[openai]"      # OpenAI
pip install "promptdrift[anthropic]"   # Anthropic / Claude
pip install "promptdrift[all]"         # All providers
```

No extra dependencies for Ollama (local models). Works out of the box.

---

## Quick Start

**1. Scaffold a starter project:**

```bash
promptdrift init --dir ./prompts
```

This creates `v1.yaml`, `v2.yaml`, and `golden.json` for you to edit.

**2. Or write configs by hand:**

```yaml
# v1.yaml
version: v1
provider: openai
model: gpt-4o-mini
system: "You are a concise assistant."
prompt_template: "{input}"
temperature: 0.0
max_tokens: 256
```

```yaml
# v2.yaml
version: v2
provider: openai
model: gpt-4o-mini
system: "You are a helpful and accurate assistant."   # ← changed
prompt_template: "Please answer: {input}"             # ← changed
temperature: 0.0
max_tokens: 256
```

**3. Create test cases:**

```json
[
  {"id": "c01", "input": "What is the capital of France?"},
  {"id": "c02", "input": "Summarize transformer architecture in 2 sentences."},
  {"id": "c03", "input": "Write a Python one-liner to reverse a string."}
]
```

**4. Run the diff:**

```bash
export OPENAI_API_KEY=sk-...
promptdrift compare v1.yaml v2.yaml --cases golden.json
```

---

## Features

### Semantic similarity (not string diff)
Uses embedding-based cosine similarity to measure *meaning* change. Falls back to TF-IDF if no embedding provider is reachable. Works offline with zero API cost.

### LLM-as-judge quality scoring
By default, promptdrift uses a fast judge model to score whether each output improved or regressed.

```bash
# Skip the judge for a faster, embedding-only run
promptdrift compare v1.yaml v2.yaml --cases golden.json --no-judge
```

### Verbose per-case output
```bash
promptdrift compare v1.yaml v2.yaml --cases golden.json --show-outputs
```

### JSON report output
```bash
promptdrift compare v1.yaml v2.yaml --cases golden.json --output report.json
```

### CI/CD ready
```bash
# Exits 1 if regressions are detected
promptdrift compare v1.yaml v2.yaml --cases golden.json --fail-on-regression
```

### Multi-provider support

| Provider | Value for `provider:` | Models |
|---|---|---|
| OpenAI | `openai` | gpt-4o, gpt-4o-mini, o1, etc. |
| Anthropic | `anthropic` | claude-opus-4-6, claude-sonnet-4-6, etc. |
| Ollama (local) | `ollama` | llama3, mistral, qwen, etc. |

Mix and match. Compare OpenAI vs Anthropic on the same test suite:

```yaml
# openai.yaml
version: openai-baseline
provider: openai
model: gpt-4o-mini
system: "You are a helpful assistant."
prompt_template: "{input}"
```

```yaml
# anthropic.yaml
version: anthropic-candidate
provider: anthropic
model: claude-haiku-4-5-20251001
system: "You are a helpful assistant."
prompt_template: "{input}"
```

```bash
promptdrift compare openai.yaml anthropic.yaml --cases golden.json
```

---

## Test Case Formats

JSON, JSONL, and YAML are all supported.

```json
// golden.json
[{"id": "c1", "input": "...", "expected": "..."}]
```

```jsonl
// golden.jsonl
{"id": "c1", "input": "..."}
{"id": "c2", "input": "..."}
```

```yaml
# golden.yaml
- id: c1
  input: "..."
  expected: "..."
```

`expected` is optional metadata. promptdrift doesn't do exact match, it does semantic comparison. The `expected` field is only used as context for the LLM judge.

---

## Config Reference

```yaml
version: v1                  # required, used in report headers
provider: openai             # required, openai | anthropic | ollama
model: gpt-4o-mini           # required, model name for the provider
prompt_template: "{input}"   # required, {input} is replaced with each test case

system: ""                   # optional system prompt
temperature: 0.0             # optional, default 0.0
max_tokens: 1024             # optional, default 1024
embedding_model: "..."       # optional override for embedding model
```

---

## Python API

```python
from promptdrift import compare, print_report

report = compare("v1.yaml", "v2.yaml", cases="golden.json", use_judge=True)
print_report(report)

print(f"Regressions: {len(report.regressions)}")
print(f"Avg drift: {report.avg_drift:.3f}")

for case in report.regressions:
    print(f"[{case.case_id}] drift={case.drift_score:.3f}")
    print(f"  Before: {case.output_a}")
    print(f"  After:  {case.output_b}")
```

---

## Why promptdrift?

Everyone evaluates LLMs before deploying. Nobody has a clean way to evaluate the *delta* between two versions of their prompt in production.

| Tool | Evaluates outputs | Detects regressions | Semantic diff | Zero config |
|---|:---:|:---:|:---:|:---:|
| PromptFoo | ✅ | partial | ❌ | ❌ |
| LangSmith | ✅ | partial | ❌ | ❌ |
| Ragas | ✅ (RAG only) | ❌ | ❌ | ❌ |
| **promptdrift** | ✅ | ✅ | ✅ | ✅ |

promptdrift is the `git diff` for your prompts. Stateless, fast, CI-friendly.

---

## Roadmap

**v0.2.0**
- HTML diff report with side-by-side view
- Reusable GitHub Action wrapper
- Google Gemini provider
- `promptdrift watch` mode for tight iteration
- CSV report export
- Per-run cost tracking

See [CHANGELOG.md](CHANGELOG.md) for what shipped.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Author

Built by [Daksh Trehan](https://dakshtrehan.com). FDE at [Abacus.AI](https://abacus.ai), 6x Snowflake certified, 50+ articles on Towards Data Science.

> "I built this because I kept breaking my prompts in prod and had no clean way to catch it."

---

<div align="center">

⭐ If promptdrift saves you from a silent regression, give it a star.

</div>
