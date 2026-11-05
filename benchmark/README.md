# Benchmark: TF-IDF drift vs sentence-transformer embeddings

promptdrift's default drift score is `1 - TF-IDF cosine similarity`: pure
Python, deterministic, free and offline. The obvious question is how much
that costs in accuracy compared with a small local embedding model. This
directory answers it on 200 labelled sentence pairs from the MRPC test
split (see [`data/SOURCE.md`](data/SOURCE.md)), and writes the numbers and
an interpretation to [`RESULTS.md`](RESULTS.md).

Nothing here calls a paid API.

## Run it

```bash
# TF-IDF leg only, no optional dependencies (about 10 ms for 200 pairs)
python benchmark/run_benchmark.py --no-embeddings

# Both legs (needs sentence-transformers and the model, see below)
python benchmark/run_benchmark.py

# Quick check on a slice, writing nothing
python benchmark/run_benchmark.py --limit 20 --no-write
```

The script reports, for each method: ROC AUC for separating paraphrases
from non-paraphrases (positive class is "not a paraphrase", score is
drift), the best single threshold and its accuracy, mean drift per class
and runtime per pair. When both legs run it also reports the Spearman rank
correlation between the two drift scores and the ten pairs where they
disagree most. AUC, threshold search and Spearman are implemented in pure
Python in `run_benchmark.py` and covered by `tests/test_benchmark.py`.

## Optional: the embedding leg

sentence-transformers is deliberately **not** a promptdrift dependency, not
even an extra: it pulls in PyTorch, which is several gigabytes. Install it
by hand in a separate environment when you want the second leg:

```bash
python -m venv .venv-bench
. .venv-bench/bin/activate
pip install -e .                     # promptdrift itself
pip install sentence-transformers    # CPU PyTorch is enough
python benchmark/run_benchmark.py    # downloads all-MiniLM-L6-v2 on first use
```

`--model` accepts any sentence-transformers model name or a local directory
holding one, which is useful on machines that cannot reach the Hugging Face
hub:

```bash
python benchmark/run_benchmark.py --model /path/to/all-MiniLM-L6-v2
```

If the package does not import or the model does not load, the script runs
the TF-IDF leg alone and records the exact reason in `RESULTS.md`; it never
fails because of the optional leg.

## Files

| File | Purpose |
|---|---|
| `run_benchmark.py` | The benchmark, metrics included |
| `data/pairs.jsonl` | 200 MRPC pairs, 100 paraphrase and 100 not, with ids and labels |
| `data/SOURCE.md` | Where the pairs come from, how they were sampled, licence note |
| `RESULTS.md` | Output of the last real run, with interpretation |
