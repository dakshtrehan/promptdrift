"""
Core data models and helpers for promptdrift.
"""
from __future__ import annotations
import json
import math
from dataclasses import dataclass, field, asdict


# ── Cosine similarity (pure Python, no numpy needed) ─────────────────────────
def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Drift classification ──────────────────────────────────────────────────────
def classify_drift(similarity: float) -> str:
    drift = 1.0 - similarity
    if drift < 0.05:
        return "none"
    if drift < 0.20:
        return "minor"
    if drift < 0.50:
        return "moderate"
    return "major"


# ── Data models ───────────────────────────────────────────────────────────────
@dataclass
class CaseResult:
    case_id: str
    input: str
    output_a: str
    output_b: str
    similarity: float       # 0.0–1.0
    drift_score: float      # 1 - similarity
    drift_level: str        # none | minor | moderate | major | regression
    judge_verdict: str | None = None      # improved | regressed | neutral
    judge_reasoning: str | None = None
    latency_a: float = 0.0
    latency_b: float = 0.0
    tokens_a: int = 0
    tokens_b: int = 0


@dataclass
class DiffReport:
    version_a: str
    version_b: str
    model_a: str
    model_b: str
    total_cases: int
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def regressions(self) -> list[CaseResult]:
        return [c for c in self.cases
                if c.drift_level == "regression" or c.judge_verdict == "regressed"]

    @property
    def improvements(self) -> list[CaseResult]:
        return [c for c in self.cases if c.judge_verdict == "improved"]

    @property
    def neutral(self) -> list[CaseResult]:
        return [c for c in self.cases
                if c not in self.regressions and c not in self.improvements]

    @property
    def avg_drift(self) -> float:
        if not self.cases:
            return 0.0
        return sum(c.drift_score for c in self.cases) / len(self.cases)

    @property
    def regression_rate(self) -> float:
        if not self.cases:
            return 0.0
        return len(self.regressions) / len(self.cases)

    def to_dict(self) -> dict:
        return {
            "version_a": self.version_a,
            "version_b": self.version_b,
            "model_a": self.model_a,
            "model_b": self.model_b,
            "total_cases": self.total_cases,
            "summary": {
                "avg_drift": round(self.avg_drift, 4),
                "regression_rate": round(self.regression_rate, 4),
                "regressions": len(self.regressions),
                "improvements": len(self.improvements),
                "neutral": len(self.neutral),
            },
            "cases": [asdict(c) for c in self.cases],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
