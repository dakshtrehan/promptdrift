# Recipe: prompt edit

Same model, edited prompt. This is the everyday case: someone rewrote the
system prompt to sound warmer, added an instruction about formatting, or
trimmed it to save tokens.

## Setup

The two configs share `provider` and `model` and differ in `system` or
`prompt_template`:

=== "v1.yaml"

    ```yaml
    version: v1
    provider: openai
    model: gpt-4o-mini
    system: "You are a support assistant. Answer in one or two sentences."
    prompt_template: "{input}"
    temperature: 0.0
    max_tokens: 300
    ```

=== "v2.yaml"

    ```yaml
    version: v2
    provider: openai
    model: gpt-4o-mini
    system: "You are a friendly support assistant. Answer warmly, and format any steps as a list."
    prompt_template: "{input}"
    temperature: 0.0
    max_tokens: 300
    ```

Templates may contain other braces (a JSON example, say); only `{input}` is
substituted.

## Iterate, then gate

While editing, keep a terminal running the comparison on every save (this
calls the model each time, so start with `--no-judge` and a small case file):

```bash
promptdrift watch compare v1.yaml v2.yaml --cases golden.json --no-judge --summary
```

See [Watch mode](watch.md). Once the prompt settles, run the full comparison
once with the judge and samples and keep the reports:

```bash
promptdrift compare v1.yaml v2.yaml --cases golden.json --samples 3 --html report.html --output report.json --max-regressions 0 --max-format-changes 0
```

## What to look for

The offline example in the repository is this exact scenario on logged
outputs (a support assistant whose prompt was edited to be warmer and to use
lists), so you can see the patterns without a key:

```bash
# docs-test: run
promptdrift diff examples/offline/outputs.jsonl --name-a v1 --name-b v2 --checks examples/offline/checks.yaml
```

| Pattern | Example case | How it shows up |
|---|---|---|
| **An instruction stopped being followed** | `refund_window`: the reply now says 14 days instead of 30 | `regex` check `pass_to_fail`, only 6 percent drift. Drift would never have caught it |
| **A format instruction worked, and broke a parser** | `order_status_json`: "Sure, here is the status:" followed by JSON missing its closing brace | `json_keys` flip and `JSON broken 1` |
| **A format instruction worked as intended** | `reset_password_steps`: prose became a list | `format changed`, REVIEW, not a regression |
| **The edit fixed something** | `cancel_plan_fee`: v1 forgot to say there is no fee | `fail_to_pass`, counted as an improvement |
| **Tone changed, facts intact** | `angry_customer` | moderate drift, checks pass, judge decides |
| **Different words, same meaning** | `reply_time`: "24 hours" versus "one business day" | the highest drift in the set. A reminder that TF-IDF is lexical |
| **The case is just noisy** | `refund_subject_line`, three samples per side | negative excess drift: the versions differ less than each differs from itself |

Rules of thumb for a prompt edit:

- Put every fact and every output contract into a [check](../concepts/flips.md)
  before you edit. Checks are what make the comparison sharp; drift alone
  tells you that the wording moved, which you already knew.
- If you asked for a format change, expect `REVIEW` from the format change
  and accept it after a look. If you did not ask for one, gate with
  `--max-format-changes 0`.
- Drift of 10 to 30 percent is normal for a reworded system prompt at
  temperature 0. Set `--max-drift` from a run you consider acceptable, not
  from a round number.
- Use [tags](tags.md) to see whether the drift concentrates in one area
  (`billing`, `structured`, `tone`). A warmer prompt that moves `tone` cases
  and leaves `structured` cases alone is working as designed.

## Keep the log

Once v2 ships, its outputs become the next baseline. Logging production
outputs per case id gives you an `outputs.jsonl` you can diff for free on
every future edit; see [Offline logs](offline-logs.md).
