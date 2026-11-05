# Recipe: provider switch

OpenAI to Anthropic, cloud to a local Ollama model, or back. The prompt may
be identical, but two things change under it: the model's vocabulary, and
how the provider treats sampling parameters.

## Setup

=== "OpenAI (baseline)"

    ```yaml
    version: openai-4o-mini
    provider: openai
    model: gpt-4o-mini
    system: "You are a concise assistant."
    prompt_template: "{input}"
    temperature: 0.0
    max_tokens: 256
    ```

=== "Anthropic (candidate)"

    ```yaml
    version: claude-haiku-4-5
    provider: anthropic
    model: claude-haiku-4-5-20251001
    system: "You are a concise assistant."
    prompt_template: "{input}"
    temperature: 0.0
    max_tokens: 256
    ```

=== "Ollama (candidate)"

    ```yaml
    version: llama3.2-local
    provider: ollama
    model: llama3.2
    system: "You are a concise assistant."
    prompt_template: "{input}"
    temperature: 0.0
    max_tokens: 256
    provider_options:
      base_url: http://localhost:11434
    ```

Install the SDKs you need (`pip install -e ".[openai,anthropic]"`), export
both keys, and run:

```bash
promptdrift compare openai.yaml anthropic.yaml --cases golden.json --samples 3 --judge-provider openai --html report.html
```

Pin the judge to one provider with `--judge-provider` so it does not depend
on which key happens to be set, and consider judging with a third model that
is neither A nor B.

## Temperature does not carry over

Read this before trusting any noise floor on a provider switch.

**Anthropic.** Claude models from 4.7 on only accept default sampling, and
`anthropic` SDK 2.x defaults to an API version that rejects `temperature`
outright. promptdrift therefore:

- sends `temperature` (and `top_p`, `top_k`) only to Claude models before 4.7,
  in the request body with the `anthropic-version: 2023-06-01` header,
- **drops the value silently for 4.7 and later**, so `temperature: 0.0` in
  your config has no effect there,
- treats model ids it cannot parse as not supporting sampling, since omitting
  the parameter is accepted everywhere.

The consequence: a Claude 4.7+ side samples at the provider's default
temperature whatever you wrote, so its noise floor is real and non-zero.
Always use `--samples 3` or more on a provider switch, and compare
`within_drift_a` with `within_drift_b` before reading `drift_score`. The
default Anthropic judge model is `claude-haiku-4-5-20251001` precisely
because it still accepts `temperature`.

For proxies or model ids promptdrift does not recognise, say explicitly
whether sampling parameters are accepted:

```yaml
provider: anthropic
model: my-proxy-model
provider_options:
  temperature_supported: true
```

**Ollama.** `temperature` and `max_tokens` are sent as `options.temperature`
and `options.num_predict`. Local models at temperature 0 are usually
deterministic on the same machine, but not across machines or quantisations.

**OpenAI.** `temperature` and `max_tokens` are passed as given. Some newer
OpenAI models reject non-default temperatures too; if a run errors, remove the
field or set it to the model's default.

`provider_options` forwards any extra keyword arguments to the provider
constructor, for example `base_url` for a self-hosted endpoint.

## What to look for

- **Vocabulary moves, so TF-IDF drift will be high.** Two providers that
  agree on every fact still phrase things differently. Expect `avg_drift` of
  30 to 60 percent and do not gate on it alone; a `--max-drift` that worked for
  prompt edits on one provider is meaningless here.
- **Formatting conventions differ.** Claude tends to add headings and
  markdown; smaller local models drop code fences or add preambles. Watch
  `format changed`, `JSON broken` and the length ratio, and gate the shapes
  you parse with `json_keys` checks and `--max-format-changes`.
- **Checks are the equaliser.** Facts, keys, required phrases and banned
  placeholders are provider-independent. A `pass_to_fail` flip is a
  regression on any provider.
- **`--similarity embedding` is tempting here** because it reads through the
  rewording. It uses the embedding provider of config A (OpenAI, or Ollama
  for Ollama configs); if none can be built the run falls back to TF-IDF and
  says so. Remember the two scales are not comparable.
- **Thinking blocks.** Anthropic responses that start with a thinking block
  are read from their `text` blocks only, so you never diff the model's
  reasoning by accident.

## Switching providers for the judge only

You can keep generation where it is and move only the judge, for cost or for
independence from the provider under test:

```bash
promptdrift compare v1.yaml v2.yaml --cases golden.json --judge-provider anthropic --judge-model claude-haiku-4-5-20251001
```

Any object with a `complete()` method works as the judge in Python; see
[LLM judge](../concepts/judge.md).
