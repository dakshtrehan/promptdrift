"""
Assertions for teams that want to gate on a ``DiffReport`` inside their own
test suite, without the pytest plugin::

    from promptdrift import compare_outputs
    from promptdrift.testing import assert_no_regressions

    def test_prompt_v2_does_not_regress():
        report = compare_outputs("outputs.jsonl", version_a="v1", version_b="v2")
        assert_no_regressions(report, max_drift=0.2)

``assert_no_regressions`` raises ``AssertionError`` with a multi-line message
that names every offending case, so the failure is readable in any runner.
Everything here uses the standard library only.
"""
from __future__ import annotations

from typing import Optional

from .core import CaseResult, DiffReport
from .export import failure_reason
from .thresholds import evaluate_thresholds

__all__ = ["assert_no_regressions", "describe_case"]


def _truncate(text: str, n: int = 160) -> str:
    """One line, at most ``n`` characters, for the A/B excerpts in messages."""
    flat = " ".join(str(text).split())
    if len(flat) <= n:
        return flat
    return flat[: n - 3] + "..."


def describe_case(case: CaseResult, width: int = 160) -> str:
    """Readable multi-line description of one case for a failure message.

    Lists the reason it counts as a regression (when it does), the drift score
    and level, the judge verdict, the check flip and truncated A/B outputs.
    """
    reason = failure_reason(case)
    lines = [f"case {case.case_id}"]
    if reason:
        lines.append(f"  reason: {reason}")
    lines.append(f"  drift:  {case.drift_score:.4f} ({case.drift_level})")
    lines.append(f"  judge:  {case.judge_verdict or 'not judged'}")
    if case.check_flip or case.checks_passed_a is not None:
        lines.append(f"  flip:   {case.check_flip or 'none'}")
    if case.format_changed:
        lines.append(f"  format: {case.signature_a} -> {case.signature_b}")
    lines.append(f"  A: {_truncate(case.output_a, width)}")
    lines.append(f"  B: {_truncate(case.output_b, width)}")
    return "\n".join(lines)


def assert_no_regressions(
    report: DiffReport,
    max_drift: Optional[float] = None,
    max_regressions: Optional[int] = 0,
    max_format_changes: Optional[int] = None,
    max_flips: Optional[int] = None,
) -> None:
    """Raise ``AssertionError`` when the report breaks any of the thresholds.

    With the defaults it fails on any regression. ``max_drift`` caps the
    average drift (0.0 to 1.0), ``max_format_changes`` caps the number of cases
    whose format signature changed and ``max_flips`` caps check flips from pass
    to fail; ``None`` disables a threshold. The gates are the same ones the
    CLI and the GitHub Action use (``promptdrift.thresholds.evaluate_thresholds``),
    so a passing test means a passing CI gate. The message lists every failed
    threshold and describes each regressed case (reason, drift, verdict, flip
    and truncated outputs).
    """
    failures = evaluate_thresholds(
        report,
        max_drift=max_drift,
        max_regressions=max_regressions,
        max_format_changes=max_format_changes,
        max_flips=max_flips,
    )
    if not failures:
        return
    lines = [f"promptdrift: {report.version_a} vs {report.version_b} failed "
             f"{len(failures)} threshold(s)"]
    lines += [f"  - {f}" for f in failures]
    if report.regressions:
        lines.append("regressed cases:")
        for c in report.regressions:
            lines += ["  " + line for line in describe_case(c).splitlines()]
    raise AssertionError("\n".join(lines))
