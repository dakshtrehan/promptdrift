# Changelog

## [0.3.0] - 2026-11-05

Not yet published to PyPI. See RELEASE.md for the release checklist.

### Added
- **GitHub Action.** `uses: dakshtrehan/promptdrift@<ref>` (a composite action at the repository root, helpers in `action/`) runs promptdrift on a pull request, posts or updates one summary comment with drift, check flips, format changes and confidence intervals, writes the job summary, uploads the HTML, JSON, JUnit and CSV reports as an artifact and fails on configurable thresholds. Fork PRs without a write token get a warning instead of a failure. See `action/README.md`. `.github/workflows/promptdrift-example.yml` dogfoods the action on the offline example in this repository's own CI.
- **Threshold gates.** `--max-drift X`, `--max-regressions N`, `--max-format-changes N` and `--max-flips N` on `compare` and `diff`; each breach prints a readable sentence to stderr and the exit code is 1. New module `promptdrift.thresholds` with `evaluate_thresholds()`, which also accepts a saved `report.json`.
- **Self-contained HTML report.** `render_html()` / `save_html()` and `--html FILE` write one file with inline CSS and vanilla JavaScript and no network requests: verdict header, summary cards, bootstrap CI bars, per-tag breakdown, and a sortable, filterable case table whose rows expand into side-by-side word-level diffs. Light and dark mode with a persisted toggle; readable with JavaScript disabled and at phone width. New module `promptdrift.html_report`.
- **CSV and JUnit XML export.** `to_csv()` / `save_csv()` and `to_junit()` / `save_junit()` in Python, `--csv FILE` and `--junit FILE` on `compare` and `diff`. The JUnit file has one testcase per case with a failure for each regression and suite properties for the headline rates and CI bounds, so Jenkins and GitHub test reporters show results natively. New module `promptdrift.export`.
- **Markdown report.** `render_markdown()` in the new module `promptdrift.markdown` renders a report (or a saved `report.json`) as GitHub flavoured Markdown with escaped outputs, for PR comments and step summaries. `report_from_dict()` rebuilds a report from `report.json`.
- **Watch mode.** `promptdrift watch [--interval S] [--clear] [--once] <compare|diff> ...` runs the wrapped command and re-runs it whenever a config, cases, outputs or checks file changes. Pure Python polling by default; `pip install promptdrift[watch]` adds watchdog for faster wake-ups. Regressions never stop the loop (the exit code is shown in the status line), Ctrl-C exits 0, and a cost warning is printed before live runs. New module `promptdrift.watch` with `watch_files()` for programmatic use.
- **pytest plugin.** `pytest --promptdrift outputs.jsonl` adds one test per case (`promptdrift::<file>::<case_id>`, failing on regressions with a readable message) plus a per-file summary test gated by `--promptdrift-max-drift` and `--promptdrift-max-regressions`; `--promptdrift-checks`, `--promptdrift-json` and `--promptdrift-html` save checks and reports. Items carry the `promptdrift` marker, work with `-m` and `-k` and appear in `--junit-xml`. A `promptdrift_report` fixture exposes the `DiffReport`. The plugin is auto-loaded once promptdrift is installed and is inert unless `--promptdrift` is given.
- `promptdrift.testing.assert_no_regressions(report, max_drift=None, max_regressions=0, max_format_changes=None, max_flips=None)` (also `from promptdrift import assert_no_regressions`) raises an `AssertionError` that lists every failed threshold and regressed case, for test suites that do not use the plugin.
- **Case tags.** An optional `tags` list on cases and offline records is carried into `CaseResult.tags` (new last field, default `None`) and drives the per-tag breakdown in the HTML report, the `tags` CSV column and the JUnit `classname`.
- `promptdrift.reporter.verdict_for(report)` returns the SAFE TO SHIP / REVIEW / DO NOT SHIP decision as `(status, reason)` so every output format shares one rule; terminal output is unchanged.
- **Documentation site** under `docs/` built with mkdocs-material and mkdocstrings (quickstart, concepts, recipes, CLI and API reference), deployed to GitHub Pages by `.github/workflows/docs.yml` on pushes to `main`.
- `examples/` directory with an index README, a zero-API-key offline scenario (`examples/offline/`: 16 logged support-assistant cases with tags, checks, a multi-sample record, `run.sh` producing JSON, HTML, CSV and JUnit reports, and a captured `expected_output.txt`), a scripted-provider live-mode example (`examples/live/mock_provider.py`), a Python API script and CI samples (`examples/ci/`).
- `benchmark/` comparing TF-IDF drift with optional sentence-transformer embeddings on 200 MRPC pairs, with pure Python ROC AUC, threshold search and Spearman; `benchmark/RESULTS.md` reports the real numbers (TF-IDF AUC 0.79) and records that the embedding leg could not download its model in the build environment.
- `tags` lists on the records in `examples/outputs.jsonl` and on every record of the new offline example.
- `tests/test_examples.py` runs every documented offline example command and `tests/test_benchmark.py` covers the benchmark metrics and script; `tests/test_action.py` drives the composite action through a mocked runner and a fake GitHub API.
- Type hints on every public function and dataclass, `SupportsComplete` and `SupportsEmbed` protocols in `promptdrift.providers` for the duck-typed judge and embedder arguments, and a strict mypy configuration (`python -m mypy promptdrift`) enforced in CI.
- Terminal demo (`docs/assets/demo.svg`) generated from the real output of the offline example, and HTML report screenshots under `docs/assets/`.
- A release checklist in `RELEASE.md`.

### Changed
- `promptdrift` exports `render_html`, `save_html`, `to_csv`, `save_csv`, `to_junit`, `save_junit`, `evaluate_thresholds`, `render_markdown` and `assert_no_regressions`.
- CI runs ruff, the test suite with coverage (promptdrift and the action helper, 90 percent minimum) on Python 3.9 to 3.13, mypy, the pytest plugin on the offline example, the examples and benchmark scripts, and the build and twine checks.
- `watchdog` is an optional extra (`pip install promptdrift[watch]`); `mypy` and `types-PyYAML` are part of the `dev` extra.
- The sdist now includes `action.yml`, `action/`, `examples/` and `benchmark/` so the test suite passes from an unpacked sdist.
- README rewritten around the offline workflow, the GitHub Action, the pytest plugin and the documentation site.

### Fixed
- `compare()` with config dicts that omit `system`, `temperature` or `max_tokens` raised `KeyError`; the same defaults as `load_config()` now apply.
- Drift scores of identical texts could be reported as `-0.0` because of floating point rounding; drift and within-version drift are now clamped at `0.0`.

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
