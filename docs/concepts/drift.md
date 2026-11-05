# Semantic drift

Semantic drift is the headline number: how far apart are the two outputs of a
case, as text?

## Similarity, drift score and levels

Per case promptdrift computes a `similarity` between 0.0 and 1.0 and defines

```text
drift_score = 1 - similarity
```

The score is bucketed into a `drift_level`:

| Level | Drift score | Reading |
|---|---|---|
| `none` | below 0.05 | The outputs are the same or nearly so |
| `minor` | 0.05 to 0.20 | A few words changed |
| `moderate` | 0.20 to 0.50 | Substantial rewording |
| `major` | 0.50 and above | Mostly different text |
| `regression` | any | Set when a deterministic check went from pass to fail, whatever the score (see [Checks and flips](flips.md)) |

The report's `avg_drift` is the mean `drift_score` over all cases, and the
`--max-drift` flag gates on that mean (`--max-drift 0.1` means 10 percent).
The 0.05 boundary between `none` and `minor` is also the threshold the
[noise floor](noise-floor.md) uses for "above noise".

## TF-IDF cosine: the default

The default similarity is the cosine of the TF-IDF vectors of the two texts,
computed in pure Python with no dependency. It is:

- **free and offline**: no API call, no model download,
- **deterministic**: the same input gives the same number on every machine,
  which is what a CI gate needs,
- **lexical**: it measures word overlap, not meaning.

That last point is the one to keep in mind. TF-IDF scores a faithful
paraphrase with different vocabulary as drift ("24 hours" versus "one
business day" is the highest-drift case in the offline example even though
the meaning is the same), and it scores a sentence that keeps the words but
flips one fact (a number, a negation) as nearly identical. The
[benchmark](../benchmark.md) measures this on a public paraphrase set: AUC
0.79 for separating paraphrases from non-paraphrases, with the two
distributions overlapping a lot.

So treat TF-IDF drift as a **change detector**, not a quality judgement:

- drift near zero means the outputs really are near identical,
- large drift means something changed and a human (or the judge) should look,
- moderate drift on its own is ambiguous.

For facts that must hold, use [checks](flips.md). For "did the meaning
change", use the [judge](judge.md) or embeddings.

## Embeddings: opt-in

Embedding similarity uses the cosine of provider embeddings (OpenAI
`text-embedding-3-small` by default, or Ollama for Ollama configs). It
captures paraphrase much better, costs money or a local model, and is not
deterministic across providers or model versions.

It is never used unless you ask for it:

=== "CLI"

    ```bash
    promptdrift compare v1.yaml v2.yaml --cases golden.json --similarity embedding
    ```

=== "Config"

    ```yaml
    version: v2
    provider: openai
    model: gpt-4o-mini
    prompt_template: "{input}"
    embedding_model: text-embedding-3-small   # setting this turns on embedding similarity
    ```

=== "Python, offline"

    ```python
    from promptdrift import compare_outputs

    # any object with embed(text) -> something with a .vector attribute
    report = compare_outputs("outputs.jsonl", embedder=my_embedder)
    ```

If embeddings are requested but no embedder can be built (no key, no SDK),
the run falls back to TF-IDF and says so. The report records the backend that
was requested in `report.similarity` and each case records what actually ran
in `similarity_method` (`tfidf`, `embedding`, or `mixed` when some sample
pairs fell back mid-case).

!!! warning "Two scales"
    TF-IDF and embedding scores live on different scales. Embedding cosines
    for related text tend to sit high (drift of a few percent), TF-IDF cosines
    spread much wider. Do not compare numbers or reuse `--max-drift` values
    across the two backends.

## Where drift is used

- the per-case `drift_score`, `drift_level` and the bar in the terminal and HTML reports,
- `avg_drift` and its [confidence interval](confidence.md),
- the [verdict](verdicts.md): average drift of 5 percent or more turns SAFE TO SHIP into REVIEW,
- the `--max-drift` gate, `assert_no_regressions(max_drift=...)` and the Action's `max-drift` input,
- the per-tag average in the HTML report and the PR comment.
