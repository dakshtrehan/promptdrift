# LLM judge

The judge is a model asked whether output B is improved, regressed or neutral
compared with output A. It is the only signal that can tell a harmless
rewording from a change of meaning, and the only one that costs money.

## When it runs

| Mode | Default | Switch |
|---|---|---|
| `promptdrift compare` (live) | on | `--no-judge` turns it off |
| `promptdrift diff` (offline) | **off** | `--judge` turns it on |
| `compare()` | `use_judge=True` | |
| `compare_outputs()` | `use_judge=False` | |
| GitHub Action | `judge: "false"` | `judge: "true"` |
| pytest plugin | never | scoring is offline only |

Offline mode exists so that nothing is called; the judge stays opt-in there.

Only cases with some drift are judged: when `drift_level` is `none` the
outputs are near identical and the judge is skipped for that case.

## Which model judges

Any provider promptdrift knows, or any object of your own with a
`complete(system, prompt, **kwargs)` method returning something with a
`.text` attribute.

- **Default provider** comes from the environment: OpenAI when
  `OPENAI_API_KEY` is set, otherwise Anthropic when `ANTHROPIC_API_KEY` is
  set. With neither the judge is skipped with a message.
- **Default model** per provider: `gpt-4o-mini`, `claude-haiku-4-5-20251001`,
  `llama3.2` for Ollama. Haiku 4.5 was chosen because it is cheap and still
  accepts `temperature`, so judge calls are as deterministic as the API allows.
- Override with `--judge-provider openai|anthropic|ollama` and `--judge-model`,
  or `judge_provider=` / `judge_model=` in Python.

```python
class MyJudge:
    def complete(self, system, prompt, **kwargs):
        ...  # must return an object with .text

report = compare_outputs("outputs.jsonl", use_judge=True, judge_provider=MyJudge())
```

`examples/live/mock_provider.py` does exactly this with a scripted judge, so
the whole judge path can be exercised without a key.

## The prompt

The judge sees the input, the `expected` answer when the case has one, output
A labelled as the baseline and output B labelled as the new version, and must
reply with JSON: `{"verdict": "improved|regressed|neutral", "reasoning": "one sentence"}`.
A reply whose verdict is not one of those three words is **unusable**, not
passed through as free text.

## Both orders

Judges have position bias: many models prefer whichever answer they read
first or last. promptdrift exposes it instead of hoping it away. By default
each case is judged twice, as (A, B) and as (B, A), and the second verdict is
flipped back into A-versus-B terms.

| A-first says | B-first says (flipped) | `judge_verdict` |
|---|---|---|
| regressed | regressed | `regressed` |
| improved | improved | `improved` |
| neutral | neutral | `neutral` |
| regressed | neutral or improved | `inconsistent` |
| a call fails or returns unusable text | anything | `error` |

`inconsistent` and `error` count as neither a regression nor an improvement:
the evidence is not reliable, so the case is left out of both counts rather
than guessed. Both are visible per case (`judge_verdict_ab`,
`judge_verdict_ba`, `judge_consistent`) and in the summary:

```json
"judge": {"judged": 4, "both_orders": 4, "inconsistent": 1, "inconsistency_rate": 0.25, "errors": 0}
```

The inconsistency rate is a direct measure of the judge's position bias on
your data. A high rate means the judge is not reliable for these cases: try
a stronger judge model, add `expected` values, or lean on checks.

An order-inconsistent verdict turns the [verdict](verdicts.md) into REVIEW.

## Cost

Judging in both orders doubles the judge calls: up to `2 x cases` per run,
on top of generation in live mode. `--judge-single-order`
(`judge_both_orders=False`) restores the single v0.1.1 call at half the cost;
in that mode a failed call maps to `neutral` for compatibility, so a broken
judge is invisible. Prefer both orders unless cost rules it out.

The judge sees the first sample of each side only, so `--samples` does not
multiply judge calls.

## What a judge cannot do

- It is a model with its own biases. Running both orders exposes position
  bias; it does not remove verbosity bias, self-preference or plain error.
- It has no ground truth beyond `expected`. Give it one when you have it.
- It is not deterministic even at temperature 0 on most providers, so two runs
  can disagree. Treat `regressed` as a strong hint, and confirm with a
  [check](flips.md) when the fact matters.
