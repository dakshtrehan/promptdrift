# Changelog

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
