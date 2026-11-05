# Live mode example

Live mode runs two prompt configs against the same test cases, calling a
model for every case and version, and scores the outputs.

| File | Purpose |
|---|---|
| `v1.yaml` | Baseline prompt config (OpenAI `gpt-4o-mini`) |
| `v2.yaml` | Edited prompt config: new system prompt and template |
| `golden.json` | Five test cases with optional `expected` values |
| `mock_provider.py` | The same `compare()` code path with a scripted fake provider and judge, no keys needed |

Copies of `v1.yaml`, `v2.yaml` and `golden.json` also live at `examples/`
because the README and the test suite reference them there.

## Running against a real model (costs money)

This needs an `OPENAI_API_KEY` and makes real API calls: with the defaults,
`2 x cases` generation calls plus up to `2 x cases` judge calls. For the five
cases in `golden.json` that is at most 20 calls to `gpt-4o-mini`, which is
cheap but not free.

```bash
pip install -e ".[openai]"
export OPENAI_API_KEY=sk-...
promptdrift compare examples/live/v1.yaml examples/live/v2.yaml --cases examples/live/golden.json
```

Options worth knowing:

```bash
# No judge: similarity, structure and checks only (half the calls)
promptdrift compare examples/live/v1.yaml examples/live/v2.yaml --cases examples/live/golden.json --no-judge

# Three samples per side to measure the noise floor (three times the generation calls)
promptdrift compare examples/live/v1.yaml examples/live/v2.yaml --cases examples/live/golden.json --samples 3

# Use Anthropic instead: edit provider/model in the YAML and export ANTHROPIC_API_KEY
```

Nothing in this repository's test suite runs the live configs against a real
provider. The test suite only runs `mock_provider.py`.

## Running the live code path with zero keys

`mock_provider.py` drives `promptdrift.compare()` with a scripted provider
and a scripted judge, so you can see every live-mode field (latency, tokens,
judge verdicts in both orders) without any API key:

```bash
# test: offline
python examples/live/mock_provider.py
```

It also shows how to inject your own provider object as the judge, which is
the supported way to plug in a model promptdrift does not know about.
