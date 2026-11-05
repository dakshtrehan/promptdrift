# Changelog

## [0.2.0] - 2026-11-04

Not yet published to PyPI. See RELEASE.md for the publishing steps.

### Added
- **Offline mode.** `compare_outputs()` / `load_outputs()` in Python and `promptdrift diff outputs.jsonl` on the CLI score stored outputs (`case_id`, `output_a`, `output_b`) without calling any model. The judge is opt-in there (`--judge`).
- **Structural drift.** Per case: format signature (markdown fence, JSON container type, list, headings, line count), JSON validity in A and B, and the length ratio B/A. Summary: format changes, JSON validity changes, JSON broken, median length ratio. New module `promptdrift.structure`.
- **Deterministic checks.** Per-case `regex`, `json_keys` and `exact` checks run on both outputs. A pass-to-fail flip counts as a regression; summary reports pass rates and flip counts. Default specs via `checks=` or `--checks FILE`. New module `promptdrift.checks`.
- **Multi-sample stability.** `compare(samples=n)` / `--samples N`, or lists of outputs in offline mode. Reports within-version drift per side, a noise floor and excess drift per case and in the summary.
- **Bootstrap confidence intervals.** Seeded 95 percent percentile bootstrap for average drift, regression rate and average excess drift (`--bootstrap N`, `--seed`). New module `promptdrift.stats`.
- **Judge in both orders.** The judge compares (A, B) and (B, A); disagreements are marked `inconsistent` and failures `error`, and neither counts as a regression. Summary reports the order-inconsistency rate. `--judge-single-order` restores the v0.1.1 single call.
- Public modules `promptdrift.similarity`, `promptdrift.judge` and `promptdrift.scoring` replace the private helpers that external code had to import.
- `provider_options` config field, forwarded to the provider constructor.
- `--version` flag, `promptdrift.__version__` from a single source.
- `examples/outputs.jsonl` offline example.

### Changed
- **The judge provider is chosen from the environment.** `judge_provider` and `judge_model` now default to `None`: OpenAI when `OPENAI_API_KEY` is set (as before), otherwise Anthropic (`claude-haiku-4-5-20251001`) when only `ANTHROPIC_API_KEY` is set. Any object with `complete()` can be passed as the judge.
- **TF-IDF is the default similarity in every case.** v0.1.1 silently switched to paid OpenAI embeddings whenever `OPENAI_API_KEY` was set. Embeddings are now opt-in via `similarity="embedding"`, `--similarity embedding` or `embedding_model` in a config. If you relied on the implicit switch, your drift numbers move to the TF-IDF scale.
- The judge runs twice per drifted case by default (both orders).
- `regressions` also includes cases where a check went from pass to fail; `improvements` also includes fail-to-pass flips. Without checks the counts are unchanged.
- The terminal verdict says REVIEW instead of SAFE TO SHIP when formats changed or judge verdicts were order-inconsistent.
- Package metadata uses an SPDX license string and declares Python 3.13 support.

### Fixed
- Anthropic provider failed on `anthropic` SDK 2.x, which has no typed `temperature` argument and defaults to API version `2026-10-21`. Sampling parameters are now sent only to Claude models before 4.7, in the request body with `anthropic-version: 2023-06-01`, and dropped for newer models that only accept default sampling.
- Anthropic responses that start with a thinking block were read incorrectly; only `text` blocks are read now.
- Empty system prompts are no longer sent to Anthropic.
- The Ollama provider ignored `temperature` and `max_tokens`.
- Prompt templates with literal braces (for example a JSON example) crashed `str.format`.
- The judge accepted any string as a verdict; unknown verdicts are now treated as unusable.
- The reporter hardcoded the version number.
- CI lint never failed (`|| true`).

### Removed
- README claims that the package is installable from PyPI, and the unverified comparison table against other tools.

## [0.1.1] - 2026-05-04

### Added
- TF-IDF fallback for semantic similarity when no embedding provider is reachable, so the tool works offline with zero API cost.
- JSONL and YAML loaders for test cases (in addition to JSON).
- `embedding_model` config field to override the default embedding model.

### Changed
- Renamed package from `promptdiff` to `promptdrift`. The previous name was claimed on PyPI by an unrelated, unmaintained 0.0.1 placeholder.
- Config schema now uses flat top-level fields (`version`, `prompt_template`, `temperature`, `max_tokens`) instead of a nested `params` block. Matches the documentation.
- Embedding routing now picks the right provider based on the config (Ollama for local, OpenAI by default for hosted).

### Fixed
- Build backend in `pyproject.toml` was set to a non-existent value, making the package uninstallable. Now correctly uses `setuptools.build_meta`.
- Bug Tracker URL in project metadata.
- README documented a config schema that did not match the actual code. Both are now aligned.
- README incorrectly described the judge flag as `--judge`. The actual flag is `--no-judge` (judging is on by default).

### Removed
- Dead `runner.py`, `config.py`, `cases.py`, `embedder.py`, `judge.py`, and `providers/` subdirectory. These were a parallel never-wired-up implementation that crashed on import.

## [0.1.0] - 2026-03-22

### Added
- Initial release as `promptdiff` (now superseded by `promptdrift`).
- `compare` CLI subcommand with semantic drift scoring via cosine similarity.
- Optional LLM-as-judge for improved/regressed/neutral verdicts.
- JSON report export.
- OpenAI, Anthropic, and Ollama provider support.
- YAML/JSON prompt config format.
- Coloured terminal report with SHIP / REVIEW / DO NOT SHIP verdicts.
- `--fail-on-regression` flag for CI integration.
- `init` subcommand to scaffold a starter project.
