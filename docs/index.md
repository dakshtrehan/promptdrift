# promptdrift

**Semantic, structural and check-based diff for LLM prompts and model outputs.**
Catch regressions before they reach production, in CI, for free.

## The problem

You tweaked a system prompt. You moved from one model to the next. You switched
providers to save money. The code still compiles, the unit tests still pass,
and nothing tells you that the assistant now quotes a 14 day refund window
instead of 30, or that the JSON it used to return has grown a friendly preamble
that breaks the parser downstream.

LLM regressions are silent. There is no stack trace, no failing assertion,
just outputs that are a little different from last week. promptdrift makes that
difference measurable and turns it into a CI gate.

## The 60 second pitch

promptdrift compares two versions of a prompt (or two sets of stored outputs)
case by case and reports:

| Signal | What it answers | Cost |
|---|---|---|
| [Semantic drift](concepts/drift.md) | How much did the wording change? TF-IDF cosine by default, embeddings opt-in | free |
| [Structural drift](concepts/structure.md) | Did the shape change? Code fences, JSON validity, lists, headings, length | free |
| [Deterministic checks](concepts/flips.md) | Did a regex, JSON key or exact match that passed before fail now? | free |
| [Noise floor](concepts/noise-floor.md) | Is the change bigger than the model's own run to run variation? | free offline, N calls live |
| [Confidence intervals](concepts/confidence.md) | How sure are the headline numbers given this many cases? | free |
| [LLM judge](concepts/judge.md) | Is B better or worse than A, asked in both orders so position bias is visible? | paid, opt-in |

Every signal feeds one [verdict](concepts/verdicts.md): **SAFE TO SHIP**,
**REVIEW** or **DO NOT SHIP**, with threshold flags that map straight to
exit codes.

```text
$ promptdrift diff examples/offline/outputs.jsonl --name-a v1 --name-b v2 --checks examples/offline/checks.yaml --summary

  A  v1  (stored)
  B  v2  (stored)
  Cases: 16

  Summary
  Avg drift         ████░░░░░░░░░░░░░░░░░░  17.9%  95% CI 7.8 to 30.4%
  Regression rate   ███░░░░░░░░░░░░░░░░░░░  12.5%  95% CI 0.0 to 31.2%

  ↑ Improved    1   ↓ Regressed   2   → Neutral    13

  Structure  format changed 3   JSON validity changed 1 (broken 1)   median length B/A 1.21x
  Checks     pass A 93.8%  B 87.5%  (16 cases)   pass→fail 2   fail→pass 1
  Stability  n=3/side   noise floor 70.6% (A 64.5%, B 76.6%)   excess drift -12.7%   above noise 0/1

  ✗  DO NOT SHIP: 2 regression(s) detected
```

![Terminal demo of promptdrift diff on the offline example](assets/demo.gif){ loading=lazy }

That run scored 16 logged support-assistant replies, called no model and cost
nothing. The same report is available as a
[self-contained HTML page](quickstart.md#3-html-junit-and-csv-outputs), JUnit XML,
CSV, Markdown for a pull request comment, and JSON.

## Where it runs

- **Terminal**: `promptdrift compare` (live, calls models) and `promptdrift diff` (offline, never calls a model).
- **CI**: a [reusable GitHub Action](reference/action.md) that posts one PR comment, uploads the HTML report and fails on thresholds.
- **Test suites**: a [pytest plugin](recipes/pytest.md) (`pytest --promptdrift outputs.jsonl`) and `assert_no_regressions()`.
- **While editing**: `promptdrift watch` re-runs on every save.

## Install

!!! warning "Not on PyPI yet"
    promptdrift has not been published to PyPI. `pip install promptdrift`
    will not work until the first release is uploaded. Install from GitHub.

```bash
pip install "git+https://github.com/dakshtrehan/promptdrift.git"
```

Or from a clone:

```bash
git clone https://github.com/dakshtrehan/promptdrift
cd promptdrift
pip install -e .
```

The only required dependency is PyYAML. Everything else is an optional extra:

| Extra | Installs | Needed for |
|---|---|---|
| `openai` | `openai>=1.0` | OpenAI generation, embeddings and judge |
| `anthropic` | `anthropic>=0.20` | Anthropic generation and judge |
| `all` | both SDKs | |
| `watch` | `watchdog>=3.0` | Faster wake-ups in [watch mode](recipes/watch.md) (polling works without it) |
| `docs` | mkdocs, mkdocs-material, mkdocstrings, pymdown-extensions | Building this site (mirrors `docs/requirements.txt`) |
| `dev` | pytest, pytest-cov, ruff, mypy, types-PyYAML, build, twine, markdown-it-py, both SDKs | Contributing |

```bash
pip install -e ".[openai]"
pip install -e ".[all,watch]"
```

Ollama needs no SDK extra, but it uses `requests` (not a promptdrift dependency; most environments already have it) and a local server at `http://localhost:11434`.

Python 3.9 to 3.13 are supported and tested in CI.

## Next steps

- [Quickstart](quickstart.md): an offline diff in under two minutes, then live mode, exports and the GitHub Action.
- [Concepts](concepts/drift.md): what each number means and when to trust it.
- [Recipes](recipes/model-upgrade.md): model upgrade, prompt edit, provider switch, CI gating, pytest, watch mode, logs, tags.
- [Reference](reference/cli.md): every CLI flag, file format, Action input and Python symbol.
- [Benchmark](benchmark.md): how TF-IDF drift fares on a public paraphrase set, reported honestly.

## Links

- Source: [github.com/dakshtrehan/promptdrift](https://github.com/dakshtrehan/promptdrift)
- Issues: [github.com/dakshtrehan/promptdrift/issues](https://github.com/dakshtrehan/promptdrift/issues)
- [Changelog](changelog.md)
- [Contributing](contributing.md)
- License: MIT
