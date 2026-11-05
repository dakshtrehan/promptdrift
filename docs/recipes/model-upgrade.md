# Recipe: model upgrade

Same prompt, new model. The provider announced that `gpt-4o-mini` is being
replaced, or you want to try the next Claude. The prompt did not change, so
any difference in the outputs comes from the model.

## Setup

Two configs that differ only in `model` (and `version`):

=== "v1.yaml (current)"

    ```yaml
    version: gpt-4o-mini
    provider: openai
    model: gpt-4o-mini
    system: "You are a support assistant for Northwind. Be precise about policy."
    prompt_template: "{input}"
    temperature: 0.0
    max_tokens: 400
    ```

=== "v2.yaml (candidate)"

    ```yaml
    version: gpt-4.1-mini
    provider: openai
    model: gpt-4.1-mini
    system: "You are a support assistant for Northwind. Be precise about policy."
    prompt_template: "{input}"
    temperature: 0.0
    max_tokens: 400
    ```

Test cases with the facts that must hold as checks:

```json
[
  {"id": "refund_window", "input": "How long do I have to request a refund?",
   "expected": "30 days", "checks": {"regex": "\\b30 days\\b"}, "tags": ["billing"]},
  {"id": "order_status_json", "input": "Return the status of order A1001 as JSON with keys order_id, status and eta.",
   "checks": {"json_keys": ["order_id", "status", "eta"]}, "tags": ["structured"]},
  {"id": "tone", "input": "Your product broke after two days. I want to talk to a person.",
   "checks": {"regex": "(?i)sorry|apolog"}, "tags": ["tone"]}
]
```

## Run

```bash
export OPENAI_API_KEY=sk-...
promptdrift compare v1.yaml v2.yaml --cases golden.json --samples 3 --html report.html --junit junit.xml --max-regressions 0
```

Three samples per side, because a new model has a different noise floor and
you want to know it before you blame the model for a change that is just
sampling. Expect `2 x 3 x cases` generation calls plus up to `2 x cases`
judge calls.

## What to look for

**Check flips first.** A `pass_to_fail` flip is a fact: the new model stopped
saying "30 days" or stopped returning the keys you parse. Those are the cases
to read before anything else. They are listed under Regressions in every
report and fail `--max-regressions 0`.

**Structural changes second.** New model families format differently: more
markdown, code fences around JSON, lists instead of prose, longer replies.
Look at `format changed N`, `JSON broken N` and `median length B/A`. A fenced
JSON reply is still valid JSON to promptdrift (one fence is stripped before
parsing), but your parser may not be as forgiving. Add
`--max-format-changes 0` if the shape matters.

**Then drift against the noise floor.** Expect the average TF-IDF drift to be
high on a model swap, often 30 to 60 percent, because a different model uses
different words even when it means the same thing. That number alone is not a
regression. Compare it with the `Stability` line: if the excess drift is
small, the new model varies against itself about as much as it differs from
the old one. If `above noise` is most of the cases, the outputs really moved.

**Judge verdicts last.** The judge runs in both orders; `regressed` with a
consistent verdict is the strongest semantic signal you have, `inconsistent`
means the judge could not tell and you should read the case yourself. The
`judge inconsistency` figure tells you how much to trust the rest.

**Latency and tokens.** Live mode records `latency_a`, `latency_b`,
`tokens_a` and `tokens_b` per case; the HTML table has a latency delta column
and the CSV has all four. A model that is 20 percent better and 3 times slower
is a product decision, and now you have the numbers.

## Pin a baseline

Save the candidate's outputs once you accept it so the next upgrade compares
against a log instead of calling the old model again:

```bash
promptdrift compare v1.yaml v2.yaml --cases golden.json --output report.json
```

The per-case `output_a` / `output_b` (and `samples_a` / `samples_b`) in
`report.json` are the material for an offline `outputs.jsonl`; see
[Offline logs](offline-logs.md) for the format.

## Provider switch as well?

If the new model lives at a different provider (OpenAI to Anthropic, cloud to
Ollama), read [Provider switch](provider-switch.md) first: sampling parameters
do not carry over, and that changes the noise floor.
