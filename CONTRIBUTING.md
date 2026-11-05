# Contributing to promptdrift

Thanks for wanting to contribute. Here's how to get set up.

## Setup

```bash
git clone https://github.com/dakshtrehan/promptdrift
cd promptdrift
pip install -e ".[dev]"
```

## Running tests

```bash
pytest -q --cov=promptdrift
```

The suite needs no API keys and makes no network calls: every provider is
mocked (the Anthropic wire-format test drives the real SDK through a mock HTTP
transport). Coverage must stay at or above 90 percent; CI enforces it on Python
3.9 to 3.13.

## Adding a provider

1. Add a class to `promptdrift/providers.py` extending `BaseProvider`.
2. Implement `complete(system, prompt, **kwargs) -> LLMResponse`.
3. Implement `embed(text) -> EmbedResponse` if the provider supports embeddings.
4. Register it in the `mapping` dict inside `get_provider()`.
5. Add an optional dependency group in `pyproject.toml` and a default judge
   model in `promptdrift/judge.py` (`DEFAULT_JUDGE_MODELS`).
6. Add tests with a fake client. Never call a real paid API in tests.

## Code style

```bash
ruff check promptdrift/ tests/
```

No em dashes in docs, please.

## Submitting

- One feature per PR.
- Tests for new functionality.
- Update README and CHANGELOG if adding a feature, and DECISIONS.md for any
  judgement call a reviewer might want to revisit.

Questions? Open an issue.
