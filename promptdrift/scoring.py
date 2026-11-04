"""
Turn one pair of outputs into a CaseResult.

Shared by live mode (``compare``) and offline mode (``compare_outputs``) so the
two paths always score the same way.
"""
from __future__ import annotations

from .core import CaseResult, classify_drift
from .judge import judge_pair
from .similarity import text_similarity
from .structure import structural_diff

__all__ = ["score_case"]


def score_case(
    case: dict,
    output_a: str,
    output_b: str,
    *,
    judge=None,
    embedder=None,
    latency_a: float = 0.0,
    latency_b: float = 0.0,
    tokens_a: int = 0,
    tokens_b: int = 0,
) -> CaseResult:
    """Score one case.

    ``case`` needs ``id``; ``input`` and ``expected`` are optional context.
    ``judge`` is a provider used as LLM judge, or ``None`` to skip judging.
    ``embedder`` is a provider with ``embed``, or ``None`` for TF-IDF.
    """
    sim, method = text_similarity(output_a, output_b, embedder)
    level = classify_drift(sim)

    verdict, reasoning = None, None
    if judge is not None and level != "none":
        verdict, reasoning = judge_pair(judge, case.get("input", ""), output_a, output_b,
                                        expected=case.get("expected"))
        if verdict == "regressed":
            level = "regression"

    return CaseResult(
        case_id=str(case["id"]),
        input=str(case.get("input", "")),
        output_a=output_a,
        output_b=output_b,
        similarity=round(sim, 4),
        drift_score=round(1.0 - sim, 4),
        drift_level=level,
        judge_verdict=verdict,
        judge_reasoning=reasoning,
        latency_a=latency_a,
        latency_b=latency_b,
        tokens_a=tokens_a,
        tokens_b=tokens_b,
        similarity_method=method,
        **structural_diff(output_a, output_b),
    )
