"""
Offline mode: score outputs you already have, without calling any model.

Input is JSONL (or a JSON array) with one record per case::

    {"case_id": "greet", "output_a": "Hello!", "output_b": "Hi there."}

For multi-sample stability, ``output_a`` and ``output_b`` may be lists of
samples: ``{"case_id": "greet", "output_a": ["Hi", "Hello"], "output_b": [...]}``.

Optional fields per record: ``input`` and ``expected`` (context for the
report and the optional judge), ``checks`` (deterministic checks, see
``promptdrift.checks``) and ``tags`` (a list of labels for per-tag breakdowns).
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import DiffReport
from .judge import resolve_judge
from .scoring import score_case

__all__ = ["load_outputs", "compare_outputs"]


def _normalise(rec: dict, i: int) -> dict:
    if not isinstance(rec, dict):
        raise ValueError(f"Record {i + 1}: must be a JSON object.")
    rec = dict(rec)
    if "case_id" not in rec:
        if "id" in rec:
            rec["case_id"] = rec["id"]
        else:
            rec["case_id"] = f"case_{i + 1}"
    for side in ("output_a", "output_b"):
        if side not in rec:
            raise ValueError(f"Record {i + 1} ({rec['case_id']}): missing '{side}'.")
        if rec[side] is None:
            rec[side] = ""
        val = rec[side]
        ok = isinstance(val, str) or (
            isinstance(val, list) and val and all(isinstance(v, (str, type(None))) for v in val))
        if not ok:
            raise ValueError(f"Record {i + 1} ({rec['case_id']}): '{side}' must be a string "
                             "or a non-empty list of strings (samples).")
        if isinstance(val, list):
            rec[side] = [v or "" for v in val]
    rec["case_id"] = str(rec["case_id"])
    return rec


def load_outputs(path: str | Path) -> list[dict]:
    """Load stored outputs from JSONL (one object per line) or a JSON array.

    Each record needs ``case_id`` (``id`` is accepted as an alias), ``output_a``
    and ``output_b``. Raises ``ValueError`` with the record number on bad input.
    """
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    stripped = text.lstrip()
    if p.suffix.lower() == ".json" or stripped.startswith("["):
        records = json.loads(text)
        if not isinstance(records, list):
            raise ValueError(f"{p.name}: JSON input must be an array of records.")
    else:
        records = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"{p.name}: line {n} is not valid JSON ({e}).") from None
    return [_normalise(r, i) for i, r in enumerate(records)]


def compare_outputs(
    outputs: str | Path | list[dict],
    *,
    version_a: str = "A",
    version_b: str = "B",
    model_a: str = "stored",
    model_b: str = "stored",
    use_judge: bool = False,
    judge_provider=None,
    judge_model: str | None = None,
    checks=None,
    bootstrap: int = 1000,
    seed: int = 0,
    judge_both_orders: bool = True,
    embedder=None,
    verbose: bool = False,
) -> DiffReport:
    """Compare stored outputs. No model is called unless ``use_judge=True``.

    ``outputs`` is a path to JSONL/JSON or a list of record dicts (see
    ``load_outputs``). ``checks`` is a default checks spec for records that do
    not carry their own. ``bootstrap`` is the number of resamples for the 95
    percent CIs (0 disables) and ``seed`` makes them reproducible. Returns the
    same ``DiffReport`` as ``compare``. Similarity is TF-IDF unless an
    ``embedder`` (any object with ``embed``) is passed.
    """
    records = load_outputs(outputs) if not isinstance(outputs, list) else [
        _normalise(r, i) for i, r in enumerate(outputs)
    ]

    judge = resolve_judge(judge_provider, judge_model, verbose=verbose) if use_judge else None

    report = DiffReport(version_a=str(version_a), version_b=str(version_b),
                        model_a=str(model_a), model_b=str(model_b),
                        total_cases=len(records), mode="offline",
                        n_bootstrap=bootstrap, bootstrap_seed=seed,
                        similarity="embedding" if embedder is not None else "tfidf")
    for i, rec in enumerate(records):
        if verbose:
            print(f"  [{i + 1}/{len(records)}] {rec['case_id']}")
        case = {"id": rec["case_id"], "input": rec.get("input", ""),
                "expected": rec.get("expected")}
        if "checks" in rec:
            case["checks"] = rec["checks"]
        if rec.get("tags") is not None:
            case["tags"] = rec["tags"]
        report.cases.append(score_case(case, rec["output_a"], rec["output_b"], judge=judge,
                                       embedder=embedder,
                                       default_checks=checks,
                                       judge_both_orders=judge_both_orders))
    return report
