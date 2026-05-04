# Contributing to promptdrift

Thanks for wanting to contribute. Here's how to get set up.

## Setup

```bash
git clone https://github.com/dakshtrehan/promptdrift
cd promptdrift
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

Core tests require no API keys.

## Adding a Provider

1. Add a new class to `promptdrift/providers.py` extending `BaseProvider`.
2. Implement `complete(system, prompt, **kwargs) -> LLMResponse`.
3. Optionally implement `embed(text) -> EmbedResponse` if the provider supports embeddings.
4. Register it in the `mapping` dict inside `get_provider()`.
5. Add a corresponding optional dependency group in `pyproject.toml`.

## Code Style

```bash
ruff check promptdrift/
black promptdrift/
```

## Submitting

- One feature per PR.
- Tests for new functionality.
- Update README and CHANGELOG if adding a feature.

Questions? Open an issue.
