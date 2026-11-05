"""
GitHub flavoured Markdown rendering of a report, for pull request comments and
``GITHUB_STEP_SUMMARY``.

    from promptdrift import compare_outputs, render_markdown

    report = compare_outputs("outputs.jsonl", version_a="v1", version_b="v2")
    print(render_markdown(report, artifact_url="https://github.com/o/r/actions/runs/1"))

The output is deterministic for the same report (no timestamps), so a CI job
that updates one comment per pull request does not churn the comment on every
run. Everything that originates in a model output or a case file (case ids,
tags, outputs, judge reasoning) is escaped so it cannot inject HTML or
Markdown, close the ``<details>`` block or break a table row.

The report may be a :class:`~promptdrift.core.DiffReport` or the dict form
written by ``--output`` / ``DiffReport.to_dict()`` (see ``report_from_dict``),
so a saved ``report.json`` can be rendered without rebuilding the report.
"""
from __future__ import annotations

import dataclasses
import html
from typing import Any, Mapping, Optional, Sequence, Union

from .core import CaseResult, DiffReport
from .export import failure_reason
from .reporter import VERDICT_LABEL, verdict_for
from .version import __version__

VERDICT_EMOJI = {"safe": "✅", "review": "⚠️", "do_not_ship": "❌"}
OUTPUT_CHARS = 160
_MD_SPECIAL = "\\`*_[]~|"


def report_from_dict(data: Mapping[str, Any]) -> DiffReport:
    """Rebuild a ``DiffReport`` from the dict written by ``DiffReport.to_dict()``.

    Unknown case keys are ignored so reports from newer versions still load.
    The bootstrap is disabled on the rebuilt report (``n_bootstrap=0``) because
    the seed is not stored; the stored ``summary.ci`` keeps the original CIs.
    """
    known = {f.name for f in dataclasses.fields(CaseResult)}
    cases = [CaseResult(**{k: v for k, v in c.items() if k in known})
             for c in data.get("cases") or []]
    return DiffReport(
        version_a=str(data.get("version_a", "A")),
        version_b=str(data.get("version_b", "B")),
        model_a=str(data.get("model_a", "?")),
        model_b=str(data.get("model_b", "?")),
        total_cases=int(data.get("total_cases", len(cases))),
        cases=cases,
        mode=str(data.get("mode", "live")),
        n_bootstrap=0,
        similarity=str(data.get("similarity", "tfidf")),
    )


# ── Escaping ──────────────────────────────────────────────────────────────────
def _one_line(s: Any) -> str:
    return " ".join(str(s).split())


def esc_text(s: Any) -> str:
    """Escape untrusted text for use in ordinary Markdown (not inside a code span)."""
    out = html.escape(_one_line(s), quote=False)
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in out)


def esc_code(s: Any, limit: Optional[int] = OUTPUT_CHARS, in_table: bool = False) -> str:
    """Wrap untrusted text in a code span that cannot be closed or escaped from.

    Newlines collapse to spaces, the text is truncated to ``limit`` characters,
    ``<``, ``>`` and ``&`` become entities so no HTML block can start, the span
    uses one more backtick than the longest run inside, and ``|`` is escaped
    when the span sits in a table cell.
    """
    text = _one_line(s)
    if limit is not None and len(text) > limit:
        text = text[:limit - 3] + "..."
    if not text:
        return "(empty)"
    text = html.escape(text, quote=False)
    if in_table:
        text = text.replace("|", "\\|")
    run, longest = 0, 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


# ── Formatting helpers ────────────────────────────────────────────────────────
def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{round(float(v) * 100, 1)}%"


def _with_ci(value: Any, ci: Optional[Mapping[str, Any]], key: str, level: float) -> str:
    base = _pct(value)
    if ci and key in ci:
        lo, hi = ci[key]
        return f"{base} ({round(level * 100)}% CI {_pct(lo)} to {_pct(hi)})"
    return base


def _case_flags(c: CaseResult) -> list[str]:
    flags = [f"drift {_pct(c.drift_score)} ({c.drift_level})"]
    if c.judge_verdict and c.judge_verdict not in ("neutral",):
        flags.append(f"judge {c.judge_verdict}")
    if c.check_flip:
        failed = [str(r.get("type", "?")) for r in (
            c.check_results_b if c.check_flip == "pass_to_fail" else c.check_results_a) or []
            if not r.get("passed")]
        detail = f": {', '.join(failed)}" if failed else ""
        flags.append(f"check {c.check_flip.replace('_', ' ')}{detail}")
    if c.format_changed:
        flags.append(f"format {c.signature_a} to {c.signature_b}")
    if c.json_valid_a and c.json_valid_b is False:
        flags.append("JSON broken")
    if c.n_samples > 1 and c.excess_drift is not None:
        flags.append(f"n={c.n_samples} noise {_pct(c.noise_floor)} "
                     f"excess {round(c.excess_drift * 100, 1):+}%")
    return flags


def _is_notable(c: CaseResult) -> bool:
    return bool(c.check_flip or c.format_changed or c.drift_level in ("major", "regression")
                or c.judge_verdict in ("inconsistent", "error", "improved")
                or (c.json_valid_a and c.json_valid_b is False))


def _case_item(c: CaseResult, regression: bool) -> str:
    icon = "❌" if regression else "•"
    head = f"- {icon} **{esc_text(c.case_id)}**"
    if c.tags:
        head += " " + " ".join(esc_code(t, limit=40) for t in c.tags)
    head += ": " + esc_text("; ".join(_case_flags(c)))
    lines = [head]
    if regression:
        lines.append(f"  - why: {esc_text(failure_reason(c) or 'regression')}")
    elif c.judge_reasoning and c.judge_verdict in ("improved", "inconsistent"):
        lines.append(f"  - judge: {esc_text(c.judge_reasoning)}")
    lines.append(f"  - A: {esc_code(c.output_a)}")
    lines.append(f"  - B: {esc_code(c.output_b)}")
    return "\n".join(lines)


# ── Sections ──────────────────────────────────────────────────────────────────
def _summary_table(rep: DiffReport, data: Mapping[str, Any]) -> str:
    s = data.get("summary") or {}
    ci = s.get("ci") or None
    level = float(ci["level"]) if ci and "level" in ci else 0.95
    structural = s.get("structural") or {}
    checks = s.get("checks")
    judge = s.get("judge")
    stability = s.get("stability")

    rows = [
        ("Avg drift", _with_ci(s.get("avg_drift", rep.avg_drift), ci, "avg_drift", level)),
        ("Regression rate",
         _with_ci(s.get("regression_rate", rep.regression_rate), ci, "regression_rate", level)),
        ("Regressions", str(len(rep.regressions))),
        ("Improvements", str(len(rep.improvements))),
        ("Neutral", str(len(rep.neutral))),
        ("Format changes", str(structural.get("format_changes", len(rep.format_changes)))),
        ("JSON broken", str(structural.get("json_broken", len(rep.json_broken)))),
    ]
    if checks:
        rows.append(("Check flips",
                     f"{checks['pass_to_fail']} pass to fail, {checks['fail_to_pass']} fail "
                     f"to pass (pass rate A {_pct(checks['pass_rate_a'])}, "
                     f"B {_pct(checks['pass_rate_b'])}, {checks['cases_with_checks']} cases)"))
    if judge:
        rate = judge.get("inconsistency_rate")
        rate_s = f" ({_pct(rate)} of two-order runs)" if rate is not None else ""
        err = f", {judge['errors']} error(s)" if judge.get("errors") else ""
        rows.append(("Judge", f"{judge['judged']} judged, {judge['inconsistent']} "
                              f"order-inconsistent{rate_s}{err}"))
    if stability:
        rows.append(("Stability",
                     f"n={stability['samples_per_side']}/side, noise floor "
                     f"{_pct(stability['avg_noise_floor'])}, excess drift "
                     f"{_pct(stability['avg_excess_drift'])}, above noise "
                     f"{stability['cases_above_noise']}/{stability['cases']}"))
    out = ["| Metric | Value |", "| --- | --- |"]
    out += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(out)


def _tag_table(rep: DiffReport) -> str:
    groups: dict[str, list[CaseResult]] = {}
    for c in rep.cases:
        for t in c.tags or []:
            groups.setdefault(str(t), []).append(c)
    if not groups:
        return ""
    is_reg = rep._is_regression
    out = ["| Tag | Cases | Avg drift | Regressions | Check flips | Format changes |",
           "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for tag in sorted(groups):
        cs = groups[tag]
        out.append(f"| {esc_code(tag, limit=40, in_table=True)} | {len(cs)} | "
                   f"{_pct(sum(c.drift_score for c in cs) / len(cs))} | "
                   f"{sum(is_reg(c) for c in cs)} | {sum(bool(c.check_flip) for c in cs)} | "
                   f"{sum(bool(c.format_changed) for c in cs)} |")
    return "\n".join(out)


def _cases_section(rep: DiffReport, max_cases: int) -> str:
    regs = rep.regressions
    reg_ids = {id(c) for c in regs}
    notable = [c for c in rep.cases if id(c) not in reg_ids and _is_notable(c)]
    if not regs and not notable:
        return ""
    budget = max(max_cases, 0)
    shown_regs = regs[:budget]
    shown_notable = notable[:max(budget - len(shown_regs), 0)]
    hidden = (len(regs) - len(shown_regs)) + (len(notable) - len(shown_notable))

    title = f"Regressions ({len(regs)})" if regs else "Notable cases"
    if regs and notable:
        title += f" and notable cases ({len(notable)})"
    lines = ["<details>", f"<summary>{html.escape(title)}</summary>", ""]
    if shown_regs:
        lines.append("**Regressions**")
        lines.append("")
        lines += [_case_item(c, True) for c in shown_regs]
        lines.append("")
    if shown_notable:
        lines.append("**Notable** (check flip, format change, major drift, judge improved, "
                     "inconsistent or error)")
        lines.append("")
        lines += [_case_item(c, False) for c in shown_notable]
        lines.append("")
    if hidden:
        lines.append(f"... and {hidden} more case(s) in the full report.")
        lines.append("")
    lines.append("</details>")
    return "\n".join(lines)


def render_markdown(
    report: Union[DiffReport, Mapping[str, Any]],
    title: str = "promptdrift report",
    max_cases: int = 25,
    artifact_url: Optional[str] = None,
    marker: Optional[str] = None,
    failures: Optional[Sequence[str]] = None,
) -> str:
    """Render a report as GitHub flavoured Markdown.

    Args:
        report: a ``DiffReport`` or the dict from ``DiffReport.to_dict()``.
        title: heading text (escaped).
        max_cases: how many regressions and notable cases to list inside the
            collapsible block before saying "and N more".
        artifact_url: link to the workflow run or artifact that holds the full
            HTML report; shown in the footer when given.
        marker: an HTML comment such as ``<!-- promptdrift-report -->`` placed
            on the first line so a CI job can find and update its own comment.
        failures: threshold failure sentences (see
            :func:`promptdrift.thresholds.evaluate_thresholds`) to show under
            the verdict.

    The verdict line uses the same rule as the terminal report
    (``reporter.verdict_for``): any regression means do not ship; drift of 5
    percent or more, a format change or an order-inconsistent judge verdict
    means review; otherwise safe to ship.
    """
    if isinstance(report, DiffReport):
        rep, data = report, report.to_dict()
    else:
        data = dict(report)
        rep = report_from_dict(data)

    status, reason = verdict_for(rep)
    parts: list[str] = []
    if marker:
        parts.append(marker.strip())
    parts.append(f"## {esc_text(title)}")
    parts.append(f"{VERDICT_EMOJI[status]} **{VERDICT_LABEL[status]}**: {esc_text(reason)}")
    mode = "offline mode (stored outputs)" if rep.mode == "offline" else "live mode"
    parts.append(
        f"A `{esc_text(rep.version_a)}` ({esc_text(rep.model_a)}) vs "
        f"B `{esc_text(rep.version_b)}` ({esc_text(rep.model_b)}), "
        f"{rep.total_cases} case(s), {mode}, similarity {esc_text(rep.similarity)}.")
    if failures:
        parts.append("**Threshold failures**\n\n"
                     + "\n".join(f"- ❌ {esc_text(f)}" for f in failures))
    parts.append(_summary_table(rep, data))
    tags = _tag_table(rep)
    if tags:
        parts.append("**By tag**\n\n" + tags)
    cases = _cases_section(rep, max_cases)
    if cases:
        parts.append(cases)
    footer = f"<sub>promptdrift v{__version__}"
    if artifact_url:
        footer += f" | [full HTML report in the run artifacts]({artifact_url})"
    footer += "</sub>"
    parts.append(footer)
    return "\n\n".join(parts) + "\n"
