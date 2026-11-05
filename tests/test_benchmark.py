"""Pure Python metrics and the benchmark script in TF-IDF only mode."""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmark"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("run_benchmark", BENCH / "run_benchmark.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── ROC AUC ──────────────────────────────────────────────────────────────────
def test_auc_perfect_and_inverted(bench):
    assert bench.roc_auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0
    assert bench.roc_auc([0.1, 0.2, 0.8, 0.9], [True, True, False, False]) == 0.0


def test_auc_ties_and_degenerate(bench):
    assert bench.roc_auc([0.5, 0.5, 0.5], [True, False, True]) == 0.5
    assert bench.roc_auc([0.1, 0.9], [True, True]) == 0.5
    assert bench.roc_auc([], []) == 0.5


def test_auc_matches_brute_force(bench):
    rng = random.Random(1)
    scores = [round(rng.random(), 2) for _ in range(60)]  # rounding creates ties
    pos = [rng.random() < 0.4 for _ in range(60)]
    p = [s for s, y in zip(scores, pos) if y]
    n = [s for s, y in zip(scores, pos) if not y]
    brute = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in p for b in n) / (len(p) * len(n))
    assert bench.roc_auc(scores, pos) == pytest.approx(brute)


# ── threshold search ─────────────────────────────────────────────────────────
def test_best_threshold_separable(bench):
    t, acc = bench.best_threshold([0.1, 0.2, 0.7, 0.9], [False, False, True, True])
    assert acc == 1.0 and t == 0.7


def test_best_threshold_prefers_lowest_on_ties_and_handles_all_negative(bench):
    t, acc = bench.best_threshold([0.3, 0.4], [False, False])
    assert acc == 1.0 and t > 0.4  # predict nothing positive
    assert bench.best_threshold([], []) == (0.0, 0.0)


# ── spearman ─────────────────────────────────────────────────────────────────
def test_spearman(bench):
    assert bench.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert bench.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert bench.spearman([1, 1, 1], [1, 2, 3]) is None
    assert bench.spearman([1], [1]) is None
    # monotone but nonlinear is still 1.0
    assert bench.spearman([1, 2, 3], [1, 100, 10000]) == pytest.approx(1.0)


def test_average_ranks(bench):
    assert bench._average_ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_cosine(bench):
    assert bench.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert bench.cosine([1, 0], [0, 1]) == 0.0
    assert bench.cosine([0, 0], [1, 1]) == 0.0


# ── data ─────────────────────────────────────────────────────────────────────
def test_pairs_file_is_balanced_and_well_formed(bench):
    pairs = bench.load_pairs(bench.DEFAULT_DATA)
    assert len(pairs) == 200
    assert sum(p["label"] for p in pairs) == 100
    assert len({p["id"] for p in pairs}) == 200
    assert all(p["sentence_a"] != p["sentence_b"] for p in pairs)


def test_load_pairs_limit_and_validation(bench, tmp_path):
    assert len(bench.load_pairs(bench.DEFAULT_DATA, limit=7)) == 7
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"sentence_a": "x", "label": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="sentence_b"):
        bench.load_pairs(bad)


# ── the script, TF-IDF only ──────────────────────────────────────────────────
def test_run_tfidf_only_on_slice(bench):
    pairs = bench.load_pairs(bench.DEFAULT_DATA, limit=20)
    res = bench.run(pairs, use_embeddings=False)
    m = res["methods"]["tfidf"]
    assert m["pairs"] == 20 and 0.0 <= m["auc"] <= 1.0 and 0.0 <= m["best_accuracy"] <= 1.0
    assert m["seconds_per_pair"] >= 0.0
    assert "embedding" not in res["methods"]
    assert res["embedding_error"].startswith("embedding leg skipped")
    assert res["spearman_tfidf_vs_embedding"] is None
    assert len(res["tfidf_hardest"]["paraphrases_scored_far"]) <= 5
    json.dumps(res)  # serialisable


def test_full_data_tfidf_separates_classes(bench):
    """TF-IDF must do clearly better than chance on the committed data (RESULTS.md says 0.79)."""
    res = bench.run(bench.load_pairs(bench.DEFAULT_DATA), use_embeddings=False)
    m = res["methods"]["tfidf"]
    assert m["auc"] > 0.7
    assert m["mean_drift_not_paraphrase"] > m["mean_drift_paraphrase"]


def test_main_writes_results_and_json(bench, tmp_path, capsys):
    results = tmp_path / "RESULTS.md"
    raw = tmp_path / "raw.json"
    rc = bench.main(["--no-embeddings", "--limit", "20", "--results", str(results),
                     "--json", str(raw), "--note", "unit test run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tfidf" in out and "wrote" in out
    text = results.read_text(encoding="utf-8")
    assert "| TF-IDF (promptdrift default) | 20 |" in text
    assert "Embedding leg: **not run**" in text
    assert "## Run notes" in text and "unit test run" in text
    assert "## Hardest pairs for TF-IDF" in text
    assert "—" not in text and "–" not in text
    assert json.loads(raw.read_text())["pairs"] == 20


def test_main_no_write(bench, tmp_path, capsys):
    rc = bench.main(["--no-embeddings", "--limit", "5", "--no-write",
                     "--results", str(tmp_path / "x.md")])
    assert rc == 0 and not (tmp_path / "x.md").exists()
    assert "embedding leg not run" in capsys.readouterr().out


def test_load_embedder_reports_import_failure(bench, monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    encode, err = bench.load_embedder("whatever")
    assert encode is None and "could not be imported" in err


def test_load_embedder_reports_model_failure(bench, monkeypatch):
    fake = types.ModuleType("sentence_transformers")

    class SentenceTransformer:
        def __init__(self, name, device=None):
            raise OSError("no such model\nsecond line")

    fake.SentenceTransformer = SentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    encode, err = bench.load_embedder("missing-model")
    assert encode is None
    assert err == "model 'missing-model' could not be loaded: OSError: no such model"


def _fake_sentence_transformers(vectors):
    fake = types.ModuleType("sentence_transformers")

    class SentenceTransformer:
        def __init__(self, name, device=None):
            self.name = name

        def encode(self, texts, batch_size=32, show_progress_bar=False):
            return [vectors(t) for t in texts]

    fake.SentenceTransformer = SentenceTransformer
    return fake


def test_embedding_leg_with_fake_model(bench, monkeypatch, tmp_path):
    """A fake sentence_transformers module exercises the whole embedding path offline."""
    def vectors(text):
        # a bag-of-characters embedding: enough to differ between pairs
        return [float(text.count(ch)) for ch in "abcdefghijklmnopqrstuvwxyz"]

    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_sentence_transformers(vectors))
    pairs = bench.load_pairs(bench.DEFAULT_DATA, limit=30)
    res = bench.run(pairs, use_embeddings=True, model_name="fake-model")
    assert res["embedding_model"] == "fake-model" and res["embedding_error"] is None
    assert set(res["methods"]) == {"tfidf", "embedding"}
    assert -1.0 <= res["spearman_tfidf_vs_embedding"] <= 1.0
    assert len(res["disagreements"]) == 10
    assert all(0.0 <= d["rank_gap"] <= 1.0 for d in res["disagreements"])
    text = bench.format_results(res, bench.DEFAULT_DATA)
    assert "Embeddings (fake-model)" in text
    assert "## Where the methods disagree most" in text
    assert "Spearman rank correlation" in text


def test_embedding_leg_encode_failure_is_recorded(bench, monkeypatch):
    def vectors(text):
        raise RuntimeError("cuda exploded")

    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_sentence_transformers(vectors))
    res = bench.run(bench.load_pairs(bench.DEFAULT_DATA, limit=4), use_embeddings=True,
                    model_name="fake")
    assert "encoding failed: RuntimeError: cuda exploded" == res["embedding_error"]
    assert "embedding" not in res["methods"]


def test_results_md_is_from_a_real_run(bench):
    """RESULTS.md must agree with the committed data and script."""
    text = (BENCH / "RESULTS.md").read_text(encoding="utf-8")
    res = bench.run(bench.load_pairs(bench.DEFAULT_DATA), use_embeddings=False)
    m = res["methods"]["tfidf"]
    assert f"| TF-IDF (promptdrift default) | 200 | {m['auc']:.3f} | {m['best_threshold']:.3f} |" in text
    assert "—" not in text
