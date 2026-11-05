"""Benchmark promptdrift's TF-IDF drift against sentence-transformer embeddings.

The question this answers: on sentence pairs with a known paraphrase label,
how well does ``1 - tfidf_similarity`` separate paraphrases from
non-paraphrases, and how much better (or not) is a small local embedding model?

Nothing here calls a paid API. The embedding leg uses sentence-transformers
with a model that runs on the CPU, and only when the package imports and the
model loads; otherwise the script runs the TF-IDF leg alone and records the
exact failure. Everything else (ROC AUC, threshold search, Spearman) is pure
Python so the script works with the core promptdrift install.

Usage, from the repository root:

    python benchmark/run_benchmark.py                   # both legs, writes benchmark/RESULTS.md
    python benchmark/run_benchmark.py --no-embeddings   # TF-IDF only
    python benchmark/run_benchmark.py --limit 20 --no-write
    python benchmark/run_benchmark.py --model /path/to/all-MiniLM-L6-v2

See benchmark/README.md for the optional install of sentence-transformers.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # so the script runs from a clone without pip install

from promptdrift.similarity import tfidf_similarity  # noqa: E402
from promptdrift.version import __version__  # noqa: E402

DEFAULT_DATA = HERE / "data" / "pairs.jsonl"
DEFAULT_RESULTS = HERE / "RESULTS.md"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


# ── data ─────────────────────────────────────────────────────────────────────
def load_pairs(path: Path, limit: Optional[int] = None) -> List[dict]:
    """Read ``{"sentence_a", "sentence_b", "label"}`` records from JSONL."""
    pairs = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            for key in ("sentence_a", "sentence_b", "label"):
                if key not in rec:
                    raise ValueError(f"{path.name}: line {n} is missing '{key}'")
            rec["label"] = int(rec["label"])
            rec.setdefault("id", f"pair-{n}")
            pairs.append(rec)
    if limit is not None:
        pairs = pairs[:limit]
    return pairs


# ── pure Python metrics ──────────────────────────────────────────────────────
def _average_ranks(values: Sequence[float]) -> List[float]:
    """1-based ranks with ties given the average rank (as in scipy.stats.rankdata)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def roc_auc(scores: Sequence[float], positives: Sequence[bool]) -> float:
    """ROC AUC: probability that a random positive scores higher than a random negative.

    Computed with the Mann-Whitney U statistic from average ranks, so ties count
    half. Returns 0.5 when one class is empty (nothing to separate).
    """
    n_pos = sum(1 for p in positives if p)
    n_neg = len(scores) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    ranks = _average_ranks(scores)
    rank_sum = sum(r for r, p in zip(ranks, positives) if p)
    u = rank_sum - n_pos * (n_pos + 1) / 2
    return u / (n_pos * n_neg)


def best_threshold(scores: Sequence[float], positives: Sequence[bool]) -> Tuple[float, float]:
    """Threshold on ``scores`` that maximises accuracy for ``score >= t`` meaning positive.

    Returns ``(threshold, accuracy)``. Candidate thresholds are every distinct
    score plus one above the maximum (predict nothing positive). Ties on
    accuracy go to the lowest threshold.
    """
    n = len(scores)
    if n == 0:
        return 0.0, 0.0
    candidates = sorted(set(scores))
    candidates.append(candidates[-1] + 1e-9)
    best_t, best_acc = candidates[0], -1.0
    for t in candidates:
        correct = sum(1 for s, p in zip(scores, positives) if (s >= t) == bool(p))
        acc = correct / n
        if acc > best_acc:
            best_t, best_acc = t, acc
    return best_t, best_acc


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation (Pearson on average ranks). None if undefined."""
    if len(x) != len(y) or len(x) < 2:
        return None
    rx, ry = _average_ranks(x), _average_ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


# ── drift methods ────────────────────────────────────────────────────────────
def tfidf_drift(pairs: Sequence[dict]) -> List[float]:
    return [1.0 - tfidf_similarity(p["sentence_a"], p["sentence_b"]) for p in pairs]


def load_embedder(model_name: str) -> Tuple[Optional[Callable[[List[str]], list]], Optional[str]]:
    """Try to build an encoder. Returns ``(encode, None)`` or ``(None, reason)``.

    ``encode(texts)`` returns one vector per text. Loading a model may download
    it from the Hugging Face hub unless ``model_name`` is a local directory;
    any failure is returned as text so the caller can report it honestly.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:  # ImportError, or torch failing to initialise
        return None, f"sentence-transformers could not be imported: {type(e).__name__}: {e}"
    try:
        model = SentenceTransformer(model_name, device="cpu")
    except Exception as e:
        msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
        return None, f"model '{model_name}' could not be loaded: {type(e).__name__}: {msg}"

    def encode(texts: List[str]) -> list:
        return [list(map(float, v)) for v in model.encode(texts, batch_size=32,
                                                           show_progress_bar=False)]

    return encode, None


def embedding_drift(pairs: Sequence[dict], encode: Callable[[List[str]], list]) -> List[float]:
    texts = [p["sentence_a"] for p in pairs] + [p["sentence_b"] for p in pairs]
    vecs = encode(texts)
    n = len(pairs)
    return [1.0 - cosine(vecs[i], vecs[n + i]) for i in range(n)]


# ── evaluation ───────────────────────────────────────────────────────────────
def evaluate(drifts: Sequence[float], labels: Sequence[int], seconds: float) -> Dict[str, float]:
    """Score one drift method. Positive class is "not a paraphrase" (label 0)."""
    not_para = [lab == 0 for lab in labels]
    t, acc = best_threshold(drifts, not_para)
    para = [d for d, lab in zip(drifts, labels) if lab == 1]
    non = [d for d, lab in zip(drifts, labels) if lab == 0]
    return {
        "pairs": len(drifts),
        "auc": roc_auc(drifts, not_para),
        "best_threshold": t,
        "best_accuracy": acc,
        "mean_drift_paraphrase": sum(para) / len(para) if para else float("nan"),
        "mean_drift_not_paraphrase": sum(non) / len(non) if non else float("nan"),
        "seconds_per_pair": seconds / len(drifts) if drifts else 0.0,
        "seconds_total": seconds,
    }


def disagreements(pairs: Sequence[dict], a: Sequence[float], b: Sequence[float],
                  top: int = 10) -> List[dict]:
    """Pairs where two methods disagree most, by rank distance (scale free)."""
    ra, rb = _average_ranks(a), _average_ranks(b)
    n = len(pairs) or 1
    rows = []
    for i, p in enumerate(pairs):
        rows.append({"id": p["id"], "label": p["label"], "sentence_a": p["sentence_a"],
                     "sentence_b": p["sentence_b"], "tfidf": a[i], "embedding": b[i],
                     "rank_gap": abs(ra[i] - rb[i]) / n})
    rows.sort(key=lambda r: -r["rank_gap"])
    return rows[:top]


def hardest_pairs(pairs: Sequence[dict], drifts: Sequence[float], top: int = 5) -> dict:
    """Paraphrases with the highest drift and non-paraphrases with the lowest.

    These are the pairs a drift threshold gets wrong first: rewordings that a
    lexical score calls far apart, and near-copies that change the meaning.
    """
    rows = [{"id": p["id"], "label": p["label"], "drift": d,
             "sentence_a": p["sentence_a"], "sentence_b": p["sentence_b"]}
            for p, d in zip(pairs, drifts)]
    para = sorted((r for r in rows if r["label"] == 1), key=lambda r: -r["drift"])[:top]
    non = sorted((r for r in rows if r["label"] == 0), key=lambda r: r["drift"])[:top]
    return {"paraphrases_scored_far": para, "not_paraphrases_scored_close": non}


def run(pairs: Sequence[dict], use_embeddings: bool = True,
        model_name: str = DEFAULT_MODEL) -> dict:
    """Run every leg and return a plain dict of results (JSON serialisable)."""
    labels = [p["label"] for p in pairs]
    t0 = time.perf_counter()
    tf = tfidf_drift(pairs)
    tf_time = time.perf_counter() - t0
    out = {
        "promptdrift_version": __version__,
        "pairs": len(pairs),
        "paraphrases": sum(labels),
        "not_paraphrases": len(labels) - sum(labels),
        "methods": {"tfidf": evaluate(tf, labels, tf_time)},
        "embedding_model": None,
        "embedding_error": None,
        "spearman_tfidf_vs_embedding": None,
        "disagreements": [],
        "tfidf_hardest": hardest_pairs(pairs, tf),
    }
    if not use_embeddings:
        out["embedding_error"] = "embedding leg skipped (--no-embeddings)"
        return out
    encode, err = load_embedder(model_name)
    if encode is None:
        out["embedding_error"] = err
        return out
    t0 = time.perf_counter()
    try:
        em = embedding_drift(pairs, encode)
    except Exception as e:
        out["embedding_error"] = f"encoding failed: {type(e).__name__}: {e}"
        return out
    em_time = time.perf_counter() - t0
    out["embedding_model"] = model_name
    out["methods"]["embedding"] = evaluate(em, labels, em_time)
    out["spearman_tfidf_vs_embedding"] = spearman(tf, em)
    out["disagreements"] = disagreements(pairs, tf, em)
    return out


# ── reporting ────────────────────────────────────────────────────────────────
def _pct(v: float) -> str:
    return f"{100 * v:.1f}%"


def format_results(res: dict, data_path: Path, note: Optional[str] = None) -> str:
    """Render a RESULTS.md with the numbers and a plain interpretation."""
    lines = [
        "# Benchmark results: TF-IDF drift vs sentence-transformer embeddings",
        "",
        f"Generated by `benchmark/run_benchmark.py` with promptdrift {res['promptdrift_version']} "
        f"on `{data_path.name}`: {res['pairs']} sentence pairs, {res['paraphrases']} labelled "
        f"paraphrase and {res['not_paraphrases']} labelled not paraphrase. "
        "See `data/SOURCE.md` for where the pairs come from.",
        "",
        "Drift is `1 - similarity`. A good drift score is high for pairs that are not "
        "paraphrases and low for pairs that are. AUC is the probability that a random "
        "non-paraphrase pair gets a higher drift than a random paraphrase pair (0.5 is "
        "chance, 1.0 is perfect). Best-threshold accuracy is the accuracy of the single "
        "cut-off on drift that classifies the most pairs correctly, chosen on this same "
        "data, so it is optimistic.",
        "",
        "## Numbers",
        "",
        "| Method | Pairs | AUC | Best threshold | Accuracy at best threshold "
        "| Mean drift, paraphrase | Mean drift, not paraphrase | Time per pair |",
        "|---|---|---|---|---|---|---|---|",
    ]
    names = {"tfidf": "TF-IDF (promptdrift default)",
             "embedding": f"Embeddings ({res['embedding_model']})"}
    for key, m in res["methods"].items():
        ms = m["seconds_per_pair"] * 1000
        lines.append(f"| {names[key]} | {m['pairs']} | {m['auc']:.3f} | {m['best_threshold']:.3f} "
                     f"| {_pct(m['best_accuracy'])} | {m['mean_drift_paraphrase']:.3f} "
                     f"| {m['mean_drift_not_paraphrase']:.3f} | {ms:.2f} ms |")
    lines.append("")
    if res["spearman_tfidf_vs_embedding"] is not None:
        lines.append(f"Spearman rank correlation between the two drift scores: "
                     f"**{res['spearman_tfidf_vs_embedding']:.3f}**.")
    else:
        lines.append(f"Embedding leg: **not run**. Reason: {res['embedding_error']}")
    lines.append("")
    if res["disagreements"]:
        lines += ["## Where the methods disagree most", "",
                  "Ranked by the gap between each pair's rank under the two methods "
                  "(1.0 would mean first under one method and last under the other).", "",
                  "| Label | TF-IDF drift | Embedding drift | Sentence A | Sentence B |",
                  "|---|---|---|---|---|"]
        for d in res["disagreements"]:
            lab = "paraphrase" if d["label"] == 1 else "not paraphrase"
            sa = d["sentence_a"].replace("|", "\\|")
            sb = d["sentence_b"].replace("|", "\\|")
            lines.append(f"| {lab} | {d['tfidf']:.3f} | {d['embedding']:.3f} | {sa} | {sb} |")
        lines.append("")
    hard = res.get("tfidf_hardest") or {}
    if hard:
        lines += ["## Hardest pairs for TF-IDF", "",
                  "Paraphrases that TF-IDF scores as far apart (a threshold would flag them):", "",
                  "| TF-IDF drift | Sentence A | Sentence B |", "|---|---|---|"]
        lines += _pair_rows(hard["paraphrases_scored_far"])
        lines += ["", "Non-paraphrases that TF-IDF scores as close (a threshold would miss them):",
                  "", "| TF-IDF drift | Sentence A | Sentence B |", "|---|---|---|"]
        lines += _pair_rows(hard["not_paraphrases_scored_close"])
        lines.append("")
    lines += interpretation(res)
    if note:
        lines += ["", "## Run notes", "", note]
    return "\n".join(lines) + "\n"


def _pair_rows(rows: Sequence[dict]) -> List[str]:
    out = []
    for r in rows:
        sa = r["sentence_a"].replace("|", "\\|")
        sb = r["sentence_b"].replace("|", "\\|")
        out.append(f"| {r['drift']:.3f} | {sa} | {sb} |")
    return out


def interpretation(res: dict) -> List[str]:
    tf = res["methods"]["tfidf"]
    em = res["methods"].get("embedding")
    lines = ["## What the numbers mean", ""]
    lines.append(
        f"* TF-IDF drift reaches an AUC of {tf['auc']:.3f} on this sample, with the best single "
        f"cut-off at drift {tf['best_threshold']:.3f} giving {_pct(tf['best_accuracy'])} accuracy. "
        "The pairs in MRPC are drawn from news articles about the same event, so even the "
        "non-paraphrases share many words; this is a hard set for a lexical method and "
        "the AUC reflects that.")
    lines.append(
        f"* Paraphrases average {tf['mean_drift_paraphrase']:.3f} TF-IDF drift and "
        f"non-paraphrases {tf['mean_drift_not_paraphrase']:.3f}. The two distributions overlap "
        "a lot: a faithful rewording with different vocabulary scores as drift, and a "
        "sentence that keeps the words but changes one fact (a number, a negation) scores "
        "as nearly identical. That is the known limitation of a word-overlap measure.")
    if em is not None:
        better = em["auc"] - tf["auc"]
        lines.append(
            f"* The embedding model reaches an AUC of {em['auc']:.3f} "
            f"({'+' if better >= 0 else ''}{better:.3f} against TF-IDF) and "
            f"{_pct(em['best_accuracy'])} accuracy at its best cut-off, at "
            f"{em['seconds_per_pair'] * 1000:.1f} ms per pair against "
            f"{tf['seconds_per_pair'] * 1000:.2f} ms for TF-IDF on this machine. "
            f"The rank correlation between the two scores is "
            f"{res['spearman_tfidf_vs_embedding']:.3f}: they agree on the broad ordering "
            "and disagree on the pairs listed above, which are mostly rewordings (embeddings "
            "call them close, TF-IDF calls them far) and same-words-different-facts pairs "
            "(the reverse).")
    else:
        lines.append(
            "* The embedding leg did not run in this environment, so there is no "
            "head-to-head number here. The reason is recorded above. Anyone with the model "
            "available locally can rerun `python benchmark/run_benchmark.py` to fill it in; "
            "the script and the data are unchanged.")
    lines += [
        "",
        "## What this means for promptdrift in CI",
        "",
        "* TF-IDF drift is fine as a **cheap change detector**: when the drift between two "
        "prompt versions is near zero the outputs really are near identical, and when it is "
        "large something changed. It is deterministic, free, offline and the same on every "
        "machine, which is what a CI gate needs. That is why it stays the default.",
        "* TF-IDF drift is **not a paraphrase detector**. A moderate drift score can be a "
        "harmless rewording or a real change of meaning, and this benchmark shows it cannot "
        "tell those apart reliably. Do not set `--max-drift` tight and expect it to catch "
        "only real regressions.",
        "* For meaning, use the signals built for it: deterministic `checks` (regex, JSON "
        "keys, exact) for facts that must hold, multi-sample noise floors to see how much "
        "drift a prompt produces against itself, and the judge or embeddings (`--similarity "
        "embedding`) when a semantic score is worth the cost. Embedding scores are on a "
        "different scale from TF-IDF, so thresholds do not carry over between them.",
        "",
        "## Caveats",
        "",
        "* 200 pairs is small; AUC differences of a few hundredths are within noise.",
        "* Best-threshold accuracy is picked on the same data it is scored on.",
        "* MRPC sentences are news text. Prompt outputs (JSON, lists, code) behave "
        "differently, and promptdrift adds structural drift and checks for exactly that.",
        "* The sample is rebalanced to 50/50; the full MRPC test split is 68 percent "
        "paraphrases.",
    ]
    return lines


def print_summary(res: dict) -> None:
    for key, m in res["methods"].items():
        print(f"{key:10} pairs={m['pairs']} auc={m['auc']:.3f} "
              f"best_threshold={m['best_threshold']:.3f} accuracy={m['best_accuracy']:.3f} "
              f"ms_per_pair={m['seconds_per_pair'] * 1000:.2f}")
    if res["spearman_tfidf_vs_embedding"] is not None:
        print(f"spearman(tfidf, embedding) = {res['spearman_tfidf_vs_embedding']:.3f}")
    if res["embedding_error"]:
        print(f"embedding leg not run: {res['embedding_error']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="pairs JSONL")
    ap.add_argument("--limit", type=int, default=None, help="use only the first N pairs")
    ap.add_argument("--no-embeddings", action="store_true", help="skip the embedding leg")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="sentence-transformers model name or local directory")
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS, help="Markdown output")
    ap.add_argument("--json", type=Path, default=None, help="also write raw results as JSON")
    ap.add_argument("--no-write", action="store_true", help="print only, write nothing")
    ap.add_argument("--note", default=None,
                    help="free text appended to the results file, for example the environment")
    args = ap.parse_args(argv)

    pairs = load_pairs(args.data, args.limit)
    res = run(pairs, use_embeddings=not args.no_embeddings, model_name=args.model)
    print_summary(res)
    if not args.no_write:
        args.results.write_text(format_results(res, args.data, args.note), encoding="utf-8")
        print(f"wrote {args.results}")
        if args.json:
            args.json.write_text(json.dumps(res, indent=2), encoding="utf-8")
            print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
