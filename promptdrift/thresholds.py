"""
Threshold gates for CI.

``evaluate_thresholds`` turns a report plus a set of optional limits into a
list of human readable failure sentences. An empty list means every gate
passed. The CLI prints each sentence to stderr and exits 1 when the list is
not empty; the GitHub Action does the same and also shows them in the PR
comment.

    from promptdrift import compare_outputs, evaluate_thresholds

    report = compare_outputs("outputs.jsonl")
    failures = evaluate_thresholds(report, max_drift=0.10, max_regressions=0)
    if failures:
        raise SystemExit("\\n".join(failures))

``max_drift`` is a fraction on the same 0.0 to 1.0 scale as ``report.avg_drift``
(0.10 means 10 percent). The count limits are inclusive: ``max_regressions=0``
fails on the first regression, ``max_regressions=2`` allows two.

The report may be a :class:`~promptdrift.core.DiffReport` or the dict form
written by ``--output`` / ``DiffReport.to_dict()``, so a saved ``report.json``
can be gated without rebuilding the report.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Union

from .core import DiffReport

#: Decimal places of ``summary.avg_drift`` in ``DiffReport.to_dict()``.
DRIFT_DIGITS = 4


def _summary_counts(report: Union[DiffReport, Mapping[str, Any]]) -> dict[str, Any]:
    """Normalise a DiffReport or its dict form into the handful of numbers gated here."""
    if isinstance(report, DiffReport):
        checks = report.checks_summary()
        return {
            # gate on the same rounded number that report.json carries, so the
            # CLI and a re-evaluation of the saved report always agree
            "avg_drift": round(report.avg_drift, DRIFT_DIGITS),
            "regressions": len(report.regressions),
            "format_changes": len(report.format_changes),
            "flips": checks["pass_to_fail"] if checks else 0,
        }
    summary = report.get("summary") or {}
    structural = summary.get("structural") or {}
    checks = summary.get("checks") or {}
    return {
        "avg_drift": float(summary.get("avg_drift") or 0.0),
        "regressions": int(summary.get("regressions") or 0),
        "format_changes": int(structural.get("format_changes") or 0),
        "flips": int(checks.get("pass_to_fail") or 0),
    }


def _pct_pair(value: float, limit: float) -> tuple[str, str]:
    """Format a measured value and its limit as percentages that never look equal.

    One decimal is used normally; when that would print the same number for
    both (10.04 percent against a 10 percent limit), more decimals are added.
    """
    for digits in range(1, 7):
        a, b = f"{value * 100:.{digits}f}%", f"{limit * 100:.{digits}f}%"
        if a != b:
            break
    if digits == 1:
        return f"{round(value * 100, 1)}%", f"{round(limit * 100, 1)}%"
    return a, b


def check_max_drift(max_drift: Optional[float]) -> Optional[float]:
    """Validate a ``max_drift`` limit: a fraction from 0.0 to 1.0, or None.

    Raises ``ValueError`` for anything else, including NaN, infinity and a
    value given in percent such as ``10``, which could otherwise never fail.
    """
    if max_drift is None:
        return None
    value = float(max_drift)
    if math.isnan(value) or not (0.0 <= value <= 1.0):
        raise ValueError(f"max-drift is a fraction between 0 and 1 (10 percent is 0.1), "
                         f"got {max_drift!r}")
    return value


def evaluate_thresholds(
    report: Union[DiffReport, Mapping[str, Any]],
    max_drift: Optional[float] = None,
    max_regressions: Optional[int] = None,
    max_format_changes: Optional[int] = None,
    max_flips: Optional[int] = None,
    fail_on_regression: bool = False,
) -> list[str]:
    """Return one readable sentence per breached threshold (empty when all pass).

    Args:
        report: a ``DiffReport`` or the dict produced by ``DiffReport.to_dict()``.
        max_drift: highest acceptable average drift as a fraction (``0.1`` is 10
            percent). ``None`` disables the gate.
        max_regressions: highest acceptable number of regressions (judge said
            regressed, or a check went from pass to fail).
        max_format_changes: highest acceptable number of cases whose output
            format signature changed between A and B.
        max_flips: highest acceptable number of check flips from pass to fail.
        fail_on_regression: the v0.2.0 CI switch; equivalent to
            ``max_regressions=0`` but reported with its own wording so the
            existing ``--fail-on-regression`` message stays recognisable.

    Each sentence names the measured value and the limit, for example
    ``"avg drift 12.3% exceeds max-drift 10.0%"`` (more decimals are shown
    when one would print the same number twice). Gates with ``None`` are
    skipped, so calling with no limits always returns ``[]``. The average drift
    is compared after rounding to 4 decimals, the precision of ``report.json``,
    so a ``DiffReport`` and its ``to_dict()`` form give the same answer.

    Raises:
        ValueError: ``max_drift`` is not a fraction from 0.0 to 1.0 (a value
            in percent such as ``10``, a negative number, NaN or infinity).
    """
    max_drift = check_max_drift(max_drift)
    counts = _summary_counts(report)
    failures: list[str] = []

    if max_drift is not None and counts["avg_drift"] > max_drift:
        measured, limit = _pct_pair(counts["avg_drift"], max_drift)
        failures.append(f"avg drift {measured} exceeds max-drift {limit}")
    if fail_on_regression and counts["regressions"] > 0:
        failures.append(
            f"{counts['regressions']} regression(s) found and fail-on-regression is set")
    if max_regressions is not None and counts["regressions"] > max_regressions:
        failures.append(
            f"{counts['regressions']} regression(s) exceed max-regressions {max_regressions}")
    if max_format_changes is not None and counts["format_changes"] > max_format_changes:
        failures.append(
            f"{counts['format_changes']} format change(s) exceed "
            f"max-format-changes {max_format_changes}")
    if max_flips is not None and counts["flips"] > max_flips:
        failures.append(
            f"{counts['flips']} check flip(s) from pass to fail exceed max-flips {max_flips}")
    return failures
