"""
Bootstrap confidence intervals, pure Python and seeded so reports are reproducible.
"""
from __future__ import annotations

import random

__all__ = ["percentile", "bootstrap_ci", "bootstrap_cis"]


def percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted list, ``q`` in [0, 1]."""
    if not sorted_vals:
        raise ValueError("percentile of empty list")
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def bootstrap_cis(columns: dict[str, list[float]], n_boot: int = 1000, seed: int = 0,
                  level: float = 0.95) -> dict[str, tuple[float, float]]:
    """Percentile bootstrap CIs of the mean for several equally long columns.

    Every column is resampled with the same case indices (a paired bootstrap), so
    intervals for metrics from the same cases are consistent with each other.
    Columns may contain ``None``; those entries are skipped within each resample.
    """
    if n_boot < 1:
        raise ValueError("n_boot must be at least 1")
    lengths = {len(v) for v in columns.values()}
    if len(lengths) > 1:
        raise ValueError("all columns must have the same length")
    n = lengths.pop() if lengths else 0
    if n == 0:
        return {}
    rng = random.Random(seed)
    stats: dict[str, list[float]] = {k: [] for k in columns}
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        for k, col in columns.items():
            vals = [col[i] for i in idx if col[i] is not None]
            if vals:
                stats[k].append(sum(vals) / len(vals))
    alpha = (1.0 - level) / 2
    out = {}
    for k, vals in stats.items():
        if vals:
            vals.sort()
            out[k] = (percentile(vals, alpha), percentile(vals, 1 - alpha))
    return out


def bootstrap_ci(values: list[float], n_boot: int = 1000, seed: int = 0,
                 level: float = 0.95) -> tuple[float, float] | None:
    """Percentile bootstrap CI of the mean of ``values`` (None if empty)."""
    return bootstrap_cis({"x": list(values)}, n_boot, seed, level).get("x")
