"""
CSV and JUnit XML export, so CI systems can show promptdrift results natively.

    from promptdrift import save_csv, save_junit

    save_csv(report, "cases.csv")        # one row per case, scalar fields only
    save_junit(report, "junit.xml")      # one <testcase> per case, regressions fail

Both exports use only the standard library. CSV rows carry the scalar
``CaseResult`` fields; the outputs themselves are left out unless
``include_outputs=True`` is passed. The JUnit file is one ``<testsuite>`` named
``promptdrift`` with suite-level ``<properties>`` for the headline numbers and
the bootstrap confidence intervals, which Jenkins, GitHub test reporters and
GitLab all accept.
"""
from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Union

from .core import CaseResult, DiffReport
from .reporter import VERDICT_LABEL, verdict_for
from .version import __version__

__all__ = ["CSV_COLUMNS", "OUTPUT_COLUMNS", "to_csv", "save_csv", "to_junit", "save_junit",
           "failure_reason"]

#: Column order of the CSV export. Outputs are appended only with ``include_outputs``.
CSV_COLUMNS = [
    "case_id", "tags", "similarity", "drift_score", "drift_level", "judge_verdict",
    "check_flip", "format_changed", "signature_a", "signature_b", "json_valid_a",
    "json_valid_b", "length_ratio", "n_samples", "noise_floor", "excess_drift",
    "latency_a", "latency_b", "tokens_a", "tokens_b", "similarity_method",
]
#: Extra columns appended when ``include_outputs=True``.
OUTPUT_COLUMNS = ["input", "output_a", "output_b"]


def _cell(value: object) -> str:
    """Render one scalar for CSV: None becomes empty, bools become true/false."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _row(c: CaseResult, include_outputs: bool) -> list[str]:
    values = {
        "case_id": c.case_id,
        "tags": ";".join(c.tags) if c.tags else "",
        "similarity": c.similarity,
        "drift_score": c.drift_score,
        "drift_level": c.drift_level,
        "judge_verdict": c.judge_verdict,
        "check_flip": c.check_flip,
        "format_changed": c.format_changed,
        "signature_a": c.signature_a,
        "signature_b": c.signature_b,
        "json_valid_a": c.json_valid_a,
        "json_valid_b": c.json_valid_b,
        "length_ratio": c.length_ratio,
        "n_samples": c.n_samples,
        "noise_floor": c.noise_floor,
        "excess_drift": c.excess_drift,
        "latency_a": c.latency_a,
        "latency_b": c.latency_b,
        "tokens_a": c.tokens_a,
        "tokens_b": c.tokens_b,
        "similarity_method": c.similarity_method,
    }
    row = [_cell(values[k]) for k in CSV_COLUMNS]
    if include_outputs:
        row += [c.input, c.output_a, c.output_b]
    return row


def to_csv(report: DiffReport, include_outputs: bool = False) -> str:
    """Render the report as CSV text, one row per case (see ``CSV_COLUMNS``).

    ``include_outputs=True`` appends the ``OUTPUT_COLUMNS`` (``input``,
    ``output_a``, ``output_b``) with the full texts (newlines are quoted by the csv module). Tags
    are joined with ``;``. Uses ``\\r\\n`` line endings as RFC 4180 expects.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    header = list(CSV_COLUMNS)
    if include_outputs:
        header += OUTPUT_COLUMNS
    writer.writerow(header)
    for c in report.cases:
        writer.writerow(_row(c, include_outputs))
    return buf.getvalue()


def save_csv(report: DiffReport, path: Union[str, Path], include_outputs: bool = False) -> None:
    """Write ``to_csv(report)`` to ``path`` as UTF-8 (``newline=""`` so rows are not doubled)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(to_csv(report, include_outputs=include_outputs))


# ── JUnit XML ────────────────────────────────────────────────────────────────
def failure_reason(c: CaseResult) -> Optional[str]:
    """Why a case counts as a regression, or None when it does not."""
    reasons: list[str] = []
    if c.judge_verdict == "regressed":
        reasons.append("judge regressed")
    if c.check_flip == "pass_to_fail":
        failed = [r.get("type", "?") for r in (c.check_results_b or []) if not r.get("passed")]
        detail = f" ({', '.join(failed)})" if failed else ""
        reasons.append(f"check pass_to_fail{detail}")
    if c.drift_level == "regression" and not reasons:
        reasons.append("drift level regression")
    if not reasons:
        return None
    out = "; ".join(reasons)
    if c.judge_reasoning and c.judge_verdict == "regressed":
        out += f": {c.judge_reasoning}"
    return out


def _case_summary(c: CaseResult) -> str:
    bits = [f"drift={c.drift_score:.4f}", f"similarity={c.similarity:.4f}",
            f"level={c.drift_level}"]
    if c.judge_verdict is not None:
        bits.append(f"judge={c.judge_verdict}")
    if c.check_flip:
        bits.append(f"checks={c.check_flip}")
    if c.format_changed:
        bits.append(f"format={c.signature_a}->{c.signature_b}")
    if c.n_samples > 1 and c.excess_drift is not None:
        bits.append(f"n={c.n_samples} noise_floor={c.noise_floor} excess={c.excess_drift}")
    if c.tags:
        bits.append("tags=" + ",".join(c.tags))
    return f"{c.case_id}: " + " ".join(bits)


def _fmt(value: object) -> str:
    return "" if value is None else str(value)


def to_junit(report: DiffReport) -> str:
    """Render the report as JUnit XML text.

    One ``<testsuite name="promptdrift">`` with ``tests``, ``failures``,
    ``errors``, ``skipped`` and ``time`` attributes, suite ``<properties>``
    (``avg_drift``, ``regression_rate``, ``verdict``, ``verdict_reason`` and
    the 95 percent CI bounds when available) and one ``<testcase>`` per case.
    The classname is ``"<version_a> vs <version_b>"``, or the case's first tag
    when it has tags. Regressions carry a ``<failure>`` whose message says
    why; judge errors carry an ``<error>``; ``<system-out>`` holds a one line
    summary per case. Everything is escaped by ``xml.etree``.
    """
    status, reason = verdict_for(report)
    cases = report.cases
    total_time = sum(c.latency_a + c.latency_b for c in cases)
    failures = sum(1 for c in cases if failure_reason(c) is not None)
    errors = sum(1 for c in cases if c.judge_verdict == "error"
                 and failure_reason(c) is None)

    suites = ET.Element("testsuites", {
        "name": "promptdrift", "tests": str(len(cases)), "failures": str(failures),
        "errors": str(errors), "time": f"{total_time:.3f}",
    })
    suite = ET.SubElement(suites, "testsuite", {
        "name": "promptdrift", "tests": str(len(cases)), "failures": str(failures),
        "errors": str(errors), "skipped": "0", "time": f"{total_time:.3f}",
        "hostname": "promptdrift", "package": "promptdrift",
    })
    props = ET.SubElement(suite, "properties")
    values = {
        "promptdrift_version": __version__,
        "version_a": report.version_a, "version_b": report.version_b,
        "model_a": report.model_a, "model_b": report.model_b,
        "mode": report.mode, "similarity": report.similarity,
        "avg_drift": round(report.avg_drift, 4),
        "regression_rate": round(report.regression_rate, 4),
        "regressions": len(report.regressions),
        "improvements": len(report.improvements),
        "neutral": len(report.neutral),
        "format_changes": len(report.format_changes),
        "verdict": VERDICT_LABEL[status], "verdict_status": status, "verdict_reason": reason,
    }
    ci = report.confidence_intervals()
    if ci:
        values["ci_level"] = ci["level"]
        for key in ("avg_drift", "regression_rate", "avg_excess_drift"):
            if key in ci:
                values[f"{key}_ci_low"], values[f"{key}_ci_high"] = ci[key]
    for name, value in values.items():
        ET.SubElement(props, "property", {"name": name, "value": _fmt(value)})

    default_class = f"{report.version_a} vs {report.version_b}"
    lines = []
    for c in cases:
        tc = ET.SubElement(suite, "testcase", {
            "name": c.case_id,
            "classname": c.tags[0] if c.tags else default_class,
            "time": f"{c.latency_a + c.latency_b:.3f}",
        })
        why = failure_reason(c)
        if why is not None:
            fail = ET.SubElement(tc, "failure", {"message": why, "type": "regression"})
            fail.text = _case_detail(c, why)
        elif c.judge_verdict == "error":
            err = ET.SubElement(tc, "error", {"message": "judge error", "type": "judge_error"})
            err.text = _case_detail(c, "the LLM judge failed on this case")
        lines.append(_case_summary(c))
    out = ET.SubElement(suite, "system-out")
    out.text = "\n".join(lines)
    ET.SubElement(suite, "system-err").text = ""

    xml = ET.tostring(suites, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n"


def _case_detail(c: CaseResult, why: str) -> str:
    return (f"{why}\n\ninput: {c.input}\n\noutput A:\n{c.output_a}\n\n"
            f"output B:\n{c.output_b}\n")


def save_junit(report: DiffReport, path: Union[str, Path]) -> None:
    """Write ``to_junit(report)`` to ``path`` as UTF-8."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(to_junit(report))
