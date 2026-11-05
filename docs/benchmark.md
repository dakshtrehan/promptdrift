# Benchmark: TF-IDF drift versus embeddings

promptdrift's default drift score is `1 - TF-IDF cosine similarity`: pure
Python, deterministic, free and offline. The obvious question is what that
costs in accuracy compared with a small local embedding model. The
`benchmark/` directory in the repository answers it on 200 labelled sentence
pairs from the MRPC paraphrase corpus, and the full numbers with their
interpretation are in
[`benchmark/RESULTS.md`](https://github.com/dakshtrehan/promptdrift/blob/main/benchmark/RESULTS.md).
This page is the summary. Nothing in the benchmark calls a paid API.

## Setup

- **Data**: 200 pairs from the MRPC test split, 100 labelled paraphrase and
  100 labelled not paraphrase, sampled with a fixed seed and rebalanced to
  50/50 (the full split is 68 percent paraphrases) so AUC and accuracy read
  cleanly. The pairs are news sentences about the same event, so even the
  non-paraphrases share many words: a hard set for a lexical method.
  `benchmark/data/SOURCE.md` records the source and the MRPC licence note
  (research use; the sample is not covered by the repository's MIT licence).
- **Task**: a good drift score should be high for non-paraphrases and low for
  paraphrases. The metrics are ROC AUC (the probability that a random
  non-paraphrase pair scores higher drift than a random paraphrase pair; 0.5
  is chance), the accuracy at the best single threshold (chosen on the same
  data, so optimistic), the mean drift per class and the time per pair.
- **Legs**: TF-IDF (promptdrift's default) and, optionally,
  `sentence-transformers/all-MiniLM-L6-v2`. sentence-transformers is not a
  dependency or an extra because it pulls in PyTorch; install it by hand in a
  separate environment.

## What was found

| Method | Pairs | AUC | Best threshold | Accuracy at best threshold | Mean drift, paraphrase | Mean drift, not paraphrase | Time per pair |
|---|---|---|---|---|---|---|---|
| TF-IDF (promptdrift default) | 200 | 0.792 | 0.315 | 73.5% | 0.250 | 0.404 | 0.02 ms |
| all-MiniLM-L6-v2 embeddings | | not run | | | | | |

**The embedding leg did not run.** sentence-transformers 6.2.0 and torch
2.15.0 installed from PyPI, but the build environment's proxy refused every
route to the model weights (huggingface.co, hf-mirror.com and an older GCS
mirror all answered 403), so `all-MiniLM-L6-v2` could not be loaded.
`RESULTS.md` records the exact error. The embedding column is therefore
absent, not estimated, and the Spearman correlation and disagreement table
the script computes when both legs run are unverified on real embeddings
(the code path is covered by a fake `sentence_transformers` module in the
tests). Anyone with hub access can fill the column in with the same data:

```bash
pip install sentence-transformers          # in a separate venv; CPU PyTorch is enough
python benchmark/run_benchmark.py          # or --model /path/to/all-MiniLM-L6-v2
```

The TF-IDF numbers are from a real run and are reproducible without any
optional dependency:

```bash
# docs-test: run
python benchmark/run_benchmark.py --no-embeddings --limit 20 --no-write
```

(`--limit 20 --no-write` is the quick check used by the test suite; drop both
flags for the full 200 pairs and a rewritten `RESULTS.md`.)

## What it means

- **TF-IDF drift separates the two classes, but not cleanly.** AUC 0.79 on
  this set; paraphrases average 0.25 drift and non-paraphrases 0.40, with the
  distributions overlapping a lot. The hardest pairs are exactly what you
  would expect: faithful rewordings with new vocabulary score as far apart
  (drift above 0.5), and sentences that keep the words but change one fact or
  add a clause score as close (drift below 0.15).
- **It is a change detector, not a paraphrase detector.** Near-zero drift
  means the outputs really are near identical, large drift means something
  changed; a moderate score can be either a harmless rewording or a real
  change of meaning, and the benchmark shows TF-IDF cannot tell them apart
  reliably. Do not set `--max-drift` tight and expect it to catch only real
  regressions.
- **That is why the other signals exist.** For facts that must hold, use
  [checks](concepts/flips.md); for how much drift a prompt produces against
  itself, the [noise floor](concepts/noise-floor.md); for meaning, the
  [judge](concepts/judge.md) or `--similarity embedding`. Embedding scores
  live on a different scale, so thresholds do not carry over between the two.
- **It stays the default** because a CI gate needs a number that is the same
  on every machine, costs nothing and needs no download. 0.02 ms per pair
  means 10,000 cases are scored in well under a second.

## Caveats

- 200 pairs is small; AUC differences of a few hundredths are within noise.
- Best-threshold accuracy is picked on the same data it is scored on.
- MRPC sentences are news text. Prompt outputs (JSON, lists, code) behave
  differently, and promptdrift adds structural drift and checks for exactly
  that.
- The sample is rebalanced to 50/50; the full MRPC test split is not.
