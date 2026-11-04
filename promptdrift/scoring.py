"""
Turn one pair of outputs into a CaseResult.

Shared by live mode (``compare``) and offline mode (``compare_outputs``) so the
two paths always score the same way.
"""
from __future__ import annotations

from .checks import check_flip, normalize_checks, run_checks
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
    default_checks=None,
) -> CaseResult:
    """Score one case.

    ``case`` needs ``id``; ``input`` and ``expected`` are optional context.
    ``judge`` is a provider used as LLM judge, or ``None`` to skip judging.
    ``embedder`` is a provider with ``embed``, or ``None`` for TF-IDF.
    ``case["checks"]`` (or ``default_checks`` when the case has none) are
    deterministic checks run on both outputs; see ``promptdrift.checks``.
    """
    spec = case.get("checks", default_checks)
    checks = normalize_checks(spec, expected=case.get("expected"))
    chk_a = run_checks(output_a, checks) if checks else None
    chk_b = run_checks(output_b, checks) if checks else None
    flip = check_flip(chk_a and chk_a["passed"], chk_b and chk_b["passed"])

    sim, method = text_similarity(output_a, output_b, embedder)
    level = classify_drift(sim)

    verdict, reasoning = None, None
    if judge is not None and level != "none":
        verdict, reasoning = judge_pair(judge, case.get("input", ""), output_a, output_b,
                                        expected=case.get("expected"))
        if verdict == "regressed":
            level = "regression"
    if flip == "pass_to_fail":
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
        checks_passed_a=chk_a["passed"] if chk_a else None,
        checks_passed_b=chk_b["passed"] if chk_b else None,
        check_flip=flip,
        check_results_a=chk_a["results"] if chk_a else None,
        check_results_b=chk_b["results"] if chk_b else None,
    )
