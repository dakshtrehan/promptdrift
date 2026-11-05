# Contributing to promptdrift

Thanks for wanting to contribute. Here is how to get set up.

## Setup

```bash
git clone https://github.com/dakshtrehan/promptdrift
cd promptdrift
pip install -e ".[dev]"
```

## Running the checks

```bash
ruff check .                                   # lint, whole repository
python -m mypy promptdrift                     # strict type check (config in pyproject.toml)
pytest -q --cov=promptdrift --cov=action       # tests with coverage
```

The suite needs no API keys and makes no network calls: every provider is
mocked (the Anthropic wire-format test drives the real SDK through a mock HTTP
transport). Run it with the keys blanked to be sure:

```bash
OPENAI_API_KEY= ANTHROPIC_API_KEY= pytest -q
```

Coverage must stay at or above 90 percent and CI enforces it on Python 3.9 to
3.13. New modules need tests at 90 percent or better. Code must stay Python
3.9 compatible: keep `from __future__ import annotations`, no `match`
statements, and `typing.Optional` / `typing.Union` where a type is evaluated
at runtime.

To try the examples and the pytest plugin locally:

```bash
bash examples/offline/run.sh                    # exits 1 on purpose, two regressions
pytest --promptdrift examples/offline/outputs.jsonl --promptdrift-checks examples/offline/checks.yaml
```

The docs site is built with `pip install -r docs/requirements.txt` and
`mkdocs build --strict`.

## Adding a provider

1. Add a class to `promptdrift/providers.py` extending `BaseProvider`.
2. Implement `complete(system, prompt, **kwargs) -> LLMResponse`.
3. Implement `embed(text) -> EmbedResponse` if the provider supports embeddings.
4. Register it in the `mapping` dict inside `get_provider()`.
5. Add an optional dependency group in `pyproject.toml` and a default judge
   model in `promptdrift/judge.py` (`DEFAULT_JUDGE_MODELS`).
6. Add tests with a fake client. Never call a real paid API in tests.

## Code style

* `ruff check .` must pass; the rule set is in `pyproject.toml`. `ruff format`
  is not enforced, so do not reformat files you are not otherwise changing.
* Public functions carry type hints; `python -m mypy promptdrift` must pass.
* No em dashes or en dashes in docs, docstrings or comments. Use commas, colons
  or full stops.
* Backward compatibility: public names, CLI flags and dataclass field order
  stay. New dataclass fields go at the end with defaults.

## Submitting

- One feature per PR.
- Tests for new functionality.
- Update README and CHANGELOG if adding a feature, and DECISIONS.md for any
  judgement call a reviewer might want to revisit.

Questions? Open an issue.
