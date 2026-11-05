"""
Turn one pair of outputs (or two sets of samples) into a CaseResult.

Shared by live mode (``compare``) and offline mode (``compare_outputs``) so the
two paths always score the same way.
"""
from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING, Any, Optional, Sequence, Union

from .checks import check_flip, normalize_checks, run_checks
from .core import CaseResult, classify_drift
from .judge import judge_both_orders as judge_two_orders
from .judge import judge_pair
from .similarity import text_similarity
from .structure import structural_diff

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from .providers import SupportsComplete, SupportsEmbed

__all__ = ["score_case", "pairwise_drift"]


class _CachedEmbedder:
    """Wrap an embedder so each distinct text is embedded once per case."""

    def __init__(self, inner: SupportsEmbed) -> None:
        self.inner = inner
        self.cache: dict[str, Any] = {}

    def embed(self, text: str) -> Any:
        if text not in self.cache:
            self.cache[text] = self.inner.embed(text)
        return self.cache[text]


def _sim(a: str, b: str, embedder: Optional[SupportsEmbed], methods: set[str]) -> float:
    s, m = text_similarity(a, b, embedder)
    methods.add(m)
    return s


def pairwise_drift(samples: Sequence[str],
                   embedder: Optional[SupportsEmbed] = None) -> float | None:
    """Mean drift over all unordered pairs within one set of samples (None if < 2)."""
    pairs = list(combinations(samples, 2))
    if not pairs:
        return None
    methods: set[str] = set()
    # max(0.0, ...) keeps identical samples at exactly 0.0 instead of -0.0 from rounding
    return max(0.0, sum(1.0 - _sim(a, b, embedder, methods) for a, b in pairs) / len(pairs))


def _as_samples(x: Union[str, Sequence[Optional[str]]]) -> list[str]:
    if isinstance(x, str):
        return [x]
    xs = [s if isinstance(s, str) else "" for s in x]
    if not xs:
        raise ValueError("A sample list must contain at least one output.")
    return xs


def score_case(
    case: dict[str, Any],
    output_a: Union[str, Sequence[Optional[str]]],
    output_b: Union[str, Sequence[Optional[str]]],
    *,
    judge: Optional[SupportsComplete] = None,
    embedder: Optional[SupportsEmbed] = None,
    latency_a: float = 0.0,
    latency_b: float = 0.0,
    tokens_a: int = 0,
    tokens_b: int = 0,
    default_checks: Union[list[dict[str, Any]], dict[str, Any], None] = None,
    judge_both_orders: bool = True,
) -> CaseResult:
    """Score one case.

    ``output_a`` / ``output_b`` are strings, or lists of samples for multi-sample
    stability. With samples, drift is the mean over all A x B pairs, the noise
    floor is the mean within-version drift, and ``excess_drift`` is drift minus
    noise floor. Structure, judge and the per-case outputs use the first sample.

    ``case`` needs ``id``; ``input`` and ``expected`` are optional context.
    ``judge`` is a provider used as LLM judge, or ``None`` to skip judging. It
    runs in both orders unless ``judge_both_orders`` is False.
    ``embedder`` is a provider with ``embed``, or ``None`` for TF-IDF.
    ``case["checks"]`` (or ``default_checks`` when the case has none) are
    deterministic checks run on every sample; a side passes when at least half
    of its samples pass. ``case["tags"]`` (a list of strings, or one string) is
    carried into ``CaseResult.tags`` for per-tag breakdowns.
    """
    sa, sb = _as_samples(output_a), _as_samples(output_b)
    first_a, first_b = sa[0], sb[0]
    multi = len(sa) > 1 or len(sb) > 1
    emb = _CachedEmbedder(embedder) if embedder is not None else None
    methods: set[str] = set()

    cross = [1.0 - _sim(a, b, emb, methods) for a in sa for b in sb]
    # cosine similarity of identical vectors can exceed 1.0 by one ulp, which would
    # make the drift of identical outputs round to -0.0; clamp it at 0.0
    drift = max(0.0, sum(cross) / len(cross))
    sim = 1.0 - drift
    level = classify_drift(sim)

    stab: dict[str, Any] = {}
    if multi:
        wa, wb = pairwise_drift(sa, emb), pairwise_drift(sb, emb)
        floors = [w for w in (wa, wb) if w is not None]
        floor = sum(floors) / len(floors) if floors else 0.0
        stab = {
            "n_samples": max(len(sa), len(sb)),
            "samples_a": sa,
            "samples_b": sb,
            "within_drift_a": None if wa is None else round(wa, 4),
            "within_drift_b": None if wb is None else round(wb, 4),
            "noise_floor": round(floor, 4),
            "excess_drift": round(drift - floor, 4),
        }

    spec = case.get("checks", default_checks)
    checks = normalize_checks(spec, expected=case.get("expected"))
    chk: dict[str, Any] = {}
    flip = None
    if checks:
        runs_a = [run_checks(s, checks) for s in sa]
        runs_b = [run_checks(s, checks) for s in sb]
        rate_a = sum(r["passed"] for r in runs_a) / len(runs_a)
        rate_b = sum(r["passed"] for r in runs_b) / len(runs_b)
        pa, pb = rate_a >= 0.5, rate_b >= 0.5
        flip = check_flip(pa, pb)
        chk = {
            "checks_passed_a": pa,
            "checks_passed_b": pb,
            "check_flip": flip,
            "check_results_a": runs_a[0]["results"],
            "check_results_b": runs_b[0]["results"],
            "check_pass_rate_a": round(rate_a, 4),
            "check_pass_rate_b": round(rate_b, 4),
        }

    verdict, reasoning = None, None
    jinfo: dict[str, Any] = {}
    if judge is not None and level != "none":
        if judge_both_orders:
            j = judge_two_orders(judge, case.get("input", ""), first_a, first_b,
                                 expected=case.get("expected"))
            verdict, reasoning = j["verdict"], j["reasoning"]
            jinfo = {"judge_verdict_ab": j["verdict_ab"], "judge_verdict_ba": j["verdict_ba"],
                     "judge_consistent": j["consistent"]}
        else:
            verdict, reasoning = judge_pair(judge, case.get("input", ""), first_a, first_b,
                                            expected=case.get("expected"))
        if verdict == "regressed":
            level = "regression"
    if flip == "pass_to_fail":
        level = "regression"

    return CaseResult(
        case_id=str(case["id"]),
        input=str(case.get("input", "")),
        output_a=first_a,
        output_b=first_b,
        similarity=round(sim, 4),
        drift_score=round(drift, 4),
        drift_level=level,
        judge_verdict=verdict,
        judge_reasoning=reasoning,
        latency_a=latency_a,
        latency_b=latency_b,
        tokens_a=tokens_a,
        tokens_b=tokens_b,
        similarity_method="embedding" if methods == {"embedding"} else
        ("tfidf" if methods == {"tfidf"} else "mixed"),
        **structural_diff(first_a, first_b),
        **chk,
        **stab,
        **jinfo,
        tags=_case_tags(case),
    )


def _valid_tags(raw: object) -> bool:
    """True when ``raw`` is a usable ``tags`` value: None, a string, or a list of scalars.

    List elements may be strings or numbers (numbers become strings); booleans,
    None and nested containers are rejected. Shared by the case and outputs
    loaders so both input paths accept the same files.
    """
    if raw is None or isinstance(raw, str):
        return True
    if not isinstance(raw, (list, tuple)):
        return False
    return all(isinstance(t, (str, int, float)) and not isinstance(t, bool) for t in raw)


def _case_tags(case: dict[str, Any]) -> list[str] | None:
    """Normalise ``case["tags"]`` to a list of strings, or None when absent.

    A string is one tag. In a list, None and empty or whitespace-only entries
    are skipped; when nothing is left the result is None ("no tags"). Any
    other type (a number, a bool, a dict) raises ``ValueError``.
    """
    raw = case.get("tags")
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"Case {case.get('id', '?')}: 'tags' must be a string or a list "
                         f"of strings, got {type(raw).__name__}.")
    tags = [str(t) for t in raw if t is not None and str(t).strip()]
    return tags or None
