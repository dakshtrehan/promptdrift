"""
Self-contained HTML report.

    from promptdrift import save_html

    save_html(report, "report.html")

The page is one file with inline CSS and vanilla JavaScript: it loads no
fonts, scripts, stylesheets or images from the network, so it can be opened
from a CI artifact or an air-gapped machine. It shows the header and verdict,
summary cards, confidence interval bars, a per-tag breakdown, and a case table
that sorts, filters and expands into a side-by-side word-level diff. Light and
dark mode follow ``prefers-color-scheme`` with a toggle that is remembered in
``localStorage``. Without JavaScript the table and every diff stay visible.

All prompt and model text goes through ``html.escape``, so an output that
contains ``<script>`` is shown as text and never executed.
"""
from __future__ import annotations

import html
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from .core import CaseResult, DiffReport
from .reporter import VERDICT_LABEL, verdict_for
from .version import __version__

__all__ = ["render_html", "save_html", "word_diff", "REPO_URL"]

REPO_URL = "https://github.com/dakshtrehan/promptdrift"

_LEVEL_RANK = {"none": 0, "minor": 1, "moderate": 2, "major": 3, "regression": 4}
_WORD_RE = re.compile(r"(\S+)(\s*)")

_e = html.escape

#: Most word pairs (words in A times words in B) the exact word diff compares.
#: ``SequenceMatcher`` gets close to quadratic on long, mostly equal texts
#: with repeated words, so above this the diff goes line by line first.
DIFF_BUDGET = 250_000


# ── Word-level diff ──────────────────────────────────────────────────────────
def _tokens(text: str) -> tuple[list[str], list[str]]:
    """Split ``text`` into words and the whitespace that follows each word.

    Returns ``(words, gaps)`` of equal length; ``gaps[i]`` is the whitespace
    after ``words[i]``. Leading whitespace is attached to a first empty word.
    """
    words: list[str] = []
    gaps: list[str] = []
    lead = len(text) - len(text.lstrip())
    if lead:
        words.append("")
        gaps.append(text[:lead])
    for m in _WORD_RE.finditer(text, lead):
        words.append(m.group(1))
        gaps.append(m.group(2))
    return words, gaps


def _render_side(words: Sequence[str], gaps: Sequence[str], marks: Sequence[bool],
                 tag: str) -> str:
    """Join words back with their own whitespace, wrapping marked runs in ``tag``.

    A run of consecutive marked words shares one tag; the whitespace after the
    last marked word stays outside it so the highlight hugs the words.
    """
    out: list[str] = []
    open_run = False
    n = len(words)
    for i, (word, gap, marked) in enumerate(zip(words, gaps, marks)):
        marked = marked and word != ""
        if marked and not open_run:
            out.append(f"<{tag}>")
            open_run = True
        out.append(_e(word))
        if open_run and not (i + 1 < n and marks[i + 1] and words[i + 1] != ""):
            out.append(f"</{tag}>")
            open_run = False
        out.append(_e(gap))
    return "".join(out)


def word_diff(a: str, b: str) -> tuple[str, str]:
    """Word-level diff of two texts as escaped HTML for the A and B sides.

    Words are compared with ``difflib.SequenceMatcher`` on whitespace-split
    tokens; each side keeps its own whitespace (newlines included). Deleted
    words are wrapped in ``<del>`` on the A side and inserted words in
    ``<ins>`` on the B side. Everything is HTML-escaped.

    Long texts stay fast: when ``len(words_a) * len(words_b)`` exceeds
    ``DIFF_BUDGET`` the texts are matched line by line first and only changed
    line blocks small enough are diffed word by word (larger changed blocks
    are marked whole). When even the line pass is too big, or the text is one
    long line, both sides are returned escaped with no marks.
    """
    da, db, _ = _word_diff(a, b)
    return da, db


def _diff_words(a: str, b: str, mark_all: bool = False) -> tuple[str, str]:
    wa, ga = _tokens(a)
    wb, gb = _tokens(b)
    if mark_all:
        return (_render_side(wa, ga, [True] * len(wa), "del"),
                _render_side(wb, gb, [True] * len(wb), "ins"))
    marks_a = [False] * len(wa)
    marks_b = [False] * len(wb)
    sm = SequenceMatcher(None, wa, wb, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        for i in range(i1, i2):
            marks_a[i] = True
        for j in range(j1, j2):
            marks_b[j] = True
    return _render_side(wa, ga, marks_a, "del"), _render_side(wb, gb, marks_b, "ins")


def _word_count(text: str) -> int:
    return len(text.split()) + 1


def _word_diff(a: str, b: str) -> tuple[str, str, str]:
    """``word_diff`` plus how it was made: ``"word"``, ``"line"`` or ``"none"``."""
    if _word_count(a) * _word_count(b) <= DIFF_BUDGET:
        da, db = _diff_words(a, b)
        return da, db, "word"
    la, lb = a.splitlines(keepends=True), b.splitlines(keepends=True)
    if (len(la) < 2 and len(lb) < 2) or len(la) * len(lb) > DIFF_BUDGET:
        return _e(a), _e(b), "none"
    out_a: list[str] = []
    out_b: list[str] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        sa, sb = "".join(la[i1:i2]), "".join(lb[j1:j2])
        if op == "equal":
            out_a.append(_e(sa))
            out_b.append(_e(sb))
            continue
        # no line in a changed block matches, so diff its words if it is small
        # enough and mark it whole otherwise
        small = _word_count(sa) * _word_count(sb) <= DIFF_BUDGET
        da, db = _diff_words(sa, sb, mark_all=not small)
        out_a.append(da)
        out_b.append(db)
    return "".join(out_a), "".join(out_b), "line"


# ── Small formatting helpers ─────────────────────────────────────────────────
def _pct(v: Optional[float], digits: int = 1) -> str:
    return "n/a" if v is None else f"{round(v * 100, digits)}%"


def _num(v: object, digits: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{v:.0f}"
    return str(v)


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge b-{_e(kind)}">{_e(text)}</span>'


def _level_badge(level: str) -> str:
    return _badge(level, level if level in _LEVEL_RANK else "none")


def _verdict_badge(verdict: Optional[str]) -> str:
    if verdict is None:
        return '<span class="muted">-</span>'
    kind = {"improved": "good", "regressed": "bad", "inconsistent": "warn",
            "error": "muted"}.get(verdict, "muted")
    return _badge(verdict, kind)


def _flip_badge(flip: Optional[str]) -> str:
    if not flip:
        return '<span class="muted">-</span>'
    return _badge(flip.replace("_", " "), "bad" if flip == "pass_to_fail" else "good")


def _pre(text: str, inner_html: Union[str, None] = None) -> str:
    """A ``<pre>`` block that wraps long lines and handles RTL text."""
    if not text:
        return '<pre class="out empty" dir="auto">(empty)</pre>'
    body = inner_html if inner_html is not None else _e(text)
    return f'<pre class="out" dir="auto">{body}</pre>'


def _tag_chips(tags: Optional[Iterable[str]]) -> str:
    if not tags:
        return ""
    return "".join(f'<span class="tag">{_e(t)}</span>' for t in tags)


def _tags_of(c: CaseResult) -> list[str]:
    """The case's tags without empty or whitespace-only entries."""
    return [t for t in (c.tags or []) if str(t).strip()]


def _clamp_pct(v: float) -> float:
    return max(0.0, min(100.0, v * 100))


# ── Sections ─────────────────────────────────────────────────────────────────
def _header(report: DiffReport) -> str:
    status, reason = verdict_for(report)
    label = VERDICT_LABEL[status]
    mode = "offline (stored outputs)" if report.mode == "offline" else report.mode
    return f"""<header>
<div class="title-row">
<h1>promptdrift report</h1>
<button id="theme" type="button" class="js-only" aria-label="Toggle dark mode">Toggle theme</button>
</div>
<div class="meta">
<div><span class="k">A</span> <strong>{_e(report.version_a)}</strong>
<span class="muted">({_e(report.model_a)})</span></div>
<div><span class="k">B</span> <strong>{_e(report.version_b)}</strong>
<span class="muted">({_e(report.model_b)})</span></div>
<div><span class="k">Mode</span> {_e(mode)}</div>
<div><span class="k">Similarity</span> {_e(report.similarity)}</div>
<div><span class="k">Cases</span> {report.total_cases}</div>
</div>
<div class="verdict v-{status}" role="status"><strong>{_e(label)}</strong>: {_e(reason)}</div>
</header>
"""  # noqa: E501


def _card(title: str, value: str, sub: str = "", kind: str = "") -> str:
    cls = f" c-{kind}" if kind else ""
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return (f'<div class="card{cls}"><div class="label">{_e(title)}</div>'
            f'<div class="value">{value}</div>{sub_html}</div>')


def _summary_cards(report: DiffReport) -> str:
    dp = report.avg_drift
    rp = report.regression_rate
    nr, ni, nn = len(report.regressions), len(report.improvements), len(report.neutral)
    cards = [
        _card("Average drift", _pct(dp),
              kind="good" if dp < 0.1 else "warn" if dp < 0.3 else "bad"),
        _card("Regression rate", _pct(rp), kind="bad" if nr else "good"),
        _card("Regressions / improvements / neutral",
              f'<span class="bad">{nr}</span> / <span class="good">{ni}</span> / {nn}'),
    ]
    st = report.structural_summary()
    if any(c.signature_a is not None for c in report.cases):
        mlr = st["median_length_ratio"]
        cards.append(_card(
            "Structural changes", str(st["format_changes"]),
            f"format changes; JSON validity changed {st['json_validity_changes']}, "
            f"broken {st['json_broken']}; median length B/A "
            f"{_num(mlr, 2) + 'x' if mlr is not None else 'n/a'}",
            kind="warn" if st["format_changes"] or st["json_broken"] else ""))
    cs = report.checks_summary()
    if cs is not None:
        cards.append(_card(
            "Check flips",
            f'<span class="bad">{cs["pass_to_fail"]}</span> pass to fail, '
            f'<span class="good">{cs["fail_to_pass"]}</span> fail to pass',
            f"pass rate A {_pct(cs['pass_rate_a'])}, B {_pct(cs['pass_rate_b'])} "
            f"over {cs['cases_with_checks']} case(s) with checks",
            kind="bad" if cs["pass_to_fail"] else ""))
    js = report.judge_summary()
    if js is not None:
        rate = js["inconsistency_rate"]
        rate_s = f" ({_pct(rate)} of two-order runs)" if rate is not None else ""
        cards.append(_card(
            "Judge inconsistency", str(js["inconsistent"]),
            f"of {js['judged']} judged{rate_s}; errors {js['errors']}",
            kind="warn" if js["inconsistent"] else ""))
    ss = report.stability_summary()
    if ss is not None:
        cards.append(_card(
            "Stability", f"noise floor {_pct(ss['avg_noise_floor'])}",
            f"excess drift {_pct(ss['avg_excess_drift'])}; "
            f"{ss['cases_above_noise']} of {ss['cases']} case(s) above noise; "
            f"n={ss['samples_per_side']} per side",
            kind="warn" if (ss["avg_excess_drift"] or 0) > 0.05 else ""))
    return '<section class="cards">' + "".join(cards) + "</section>\n"


def _ci_bar(label: str, value: float, ci: Optional[Sequence[float]], kind: str) -> str:
    fill = _clamp_pct(value)
    marker = ""
    ci_text = ""
    if ci:
        lo, hi = ci
        left, right = _clamp_pct(lo), _clamp_pct(hi)
        width = max(right - left, 0.4)
        marker = (f'<div class="ci-range" style="left:{left:.2f}%;width:{width:.2f}%"'
                  f' title="95% CI {_pct(lo)} to {_pct(hi)}"></div>')
        ci_text = f'<span class="muted">95% CI {_pct(lo)} to {_pct(hi)}</span>'
    return (f'<div class="ci"><div class="ci-label">{_e(label)}</div>'
            f'<div class="ci-track"><div class="ci-fill f-{kind}" style="width:{fill:.2f}%"></div>'
            f'{marker}</div><div class="ci-num"><strong>{_pct(value)}</strong> {ci_text}</div>'
            '</div>')


def _ci_section(report: DiffReport) -> str:
    ci = report.confidence_intervals() or {}
    dp = report.avg_drift
    bars = [
        _ci_bar("Average drift", dp, ci.get("avg_drift"),
                "good" if dp < 0.1 else "warn" if dp < 0.3 else "bad"),
        _ci_bar("Regression rate", report.regression_rate, ci.get("regression_rate"), "bad"),
    ]
    if "avg_excess_drift" in ci:
        vals = [c.excess_drift for c in report.cases if c.excess_drift is not None]
        avg = sum(vals) / len(vals) if vals else 0.0
        bars.append(_ci_bar("Average excess drift", avg, ci["avg_excess_drift"], "warn"))
    note = (f'<p class="muted small">Range markers show the {int(ci.get("level", 0.95) * 100)}% '
            f'bootstrap interval over {ci.get("n_bootstrap", 0)} resamples of the cases.</p>'
            if ci else '<p class="muted small">Bootstrap confidence intervals disabled.</p>')
    return '<section><h2>Headline rates</h2>' + "".join(bars) + note + "</section>\n"


def _tag_section(report: DiffReport) -> str:
    rows: dict[str, list[CaseResult]] = {}
    for c in report.cases:
        for t in _tags_of(c):
            rows.setdefault(t, []).append(c)
    if not rows:
        return ""
    is_reg = report._is_regression
    body = []
    for tag in sorted(rows):
        cs = rows[tag]
        n = len(cs)
        body.append(
            f"<tr><td>{_tag_chips([tag])}</td><td class=\"num\">{n}</td>"
            f"<td class=\"num\">{_pct(sum(c.drift_score for c in cs) / n)}</td>"
            f"<td class=\"num\">{sum(is_reg(c) for c in cs)}</td>"
            f"<td class=\"num\">{sum(bool(c.check_flip) for c in cs)}</td>"
            f"<td class=\"num\">{sum(bool(c.format_changed) for c in cs)}</td></tr>")
    return ("<section><h2>By tag</h2><table class=\"tags\"><thead><tr><th>Tag</th>"
            "<th class=\"num\">Cases</th><th class=\"num\">Avg drift</th>"
            "<th class=\"num\">Regressions</th><th class=\"num\">Check flips</th>"
            "<th class=\"num\">Format changes</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table></section>\n")


def _checks_table(c: CaseResult) -> str:
    if c.check_results_a is None and c.check_results_b is None:
        return ""
    ra = c.check_results_a or []
    rb = c.check_results_b or []
    rows = []
    for i in range(max(len(ra), len(rb))):
        a = ra[i] if i < len(ra) else {}
        b = rb[i] if i < len(rb) else {}
        t = a.get("type") or b.get("type") or "?"
        rows.append(
            f"<tr><td>{_e(str(t))}</td><td>{_e(str(a.get('detail', '')))}</td>"
            f"<td>{_pass(a.get('passed'))}</td><td>{_e(str(b.get('detail', '')))}</td>"
            f"<td>{_pass(b.get('passed'))}</td></tr>")
    rates = ""
    if c.check_pass_rate_a is not None:
        rates = (f'<p class="small muted">Pass rate over samples: A {_pct(c.check_pass_rate_a)}, '
                 f'B {_pct(c.check_pass_rate_b)}</p>')
    return ("<h4>Checks</h4><table class=\"checks\"><thead><tr><th>Type</th><th>Detail A</th>"
            "<th>A</th><th>Detail B</th><th>B</th></tr></thead><tbody>" + "".join(rows)
            + "</tbody></table>" + rates)


def _pass(v: Optional[bool]) -> str:
    if v is None:
        return '<span class="muted">-</span>'
    return '<span class="good">pass</span>' if v else '<span class="bad">fail</span>'


def _judge_block(c: CaseResult) -> str:
    if c.judge_verdict is None:
        return ""
    orders = ""
    if c.judge_verdict_ab is not None or c.judge_verdict_ba is not None:
        cons = ("consistent" if c.judge_consistent else "inconsistent"
                if c.judge_consistent is not None else "n/a")
        orders = (f' <span class="muted small">(A then B: {_e(str(c.judge_verdict_ab))}, '
                  f'B then A: {_e(str(c.judge_verdict_ba))}, {cons})</span>')
    reason = f'<p class="reason">{_e(c.judge_reasoning)}</p>' if c.judge_reasoning else ""
    return f"<h4>Judge</h4><p>{_verdict_badge(c.judge_verdict)}{orders}</p>{reason}"


def _structure_block(c: CaseResult) -> str:
    if c.signature_a is None and c.signature_b is None and c.length_ratio is None:
        return ""
    items = [
        f"signature A <code>{_e(str(c.signature_a))}</code>, "
        f"B <code>{_e(str(c.signature_b))}</code>"
        + (' <span class="warn">(changed)</span>' if c.format_changed else ""),
        f"JSON valid A {_num(c.json_valid_a)}, B {_num(c.json_valid_b)}"
        + (' <span class="warn">(changed)</span>' if c.json_validity_changed else ""),
        f"length ratio B/A {_num(c.length_ratio, 2)}",
    ]
    return "<h4>Structure</h4><ul class=\"kv\">" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _samples_block(c: CaseResult) -> str:
    if c.n_samples <= 1:
        return ""
    items = [
        f"samples per side {c.n_samples}",
        f"within-version drift A {_pct(c.within_drift_a)}, B {_pct(c.within_drift_b)}",
        f"noise floor {_pct(c.noise_floor)}, excess drift {_pct(c.excess_drift)}",
    ]
    lists = ""
    for side, samples in (("A", c.samples_a), ("B", c.samples_b)):
        if samples:
            lists += (f"<details><summary>All {len(samples)} samples of {side}</summary>"
                      + "".join(_pre(s) for s in samples) + "</details>")
    return ("<h4>Stability</h4><ul class=\"kv\">"
            + "".join(f"<li>{i}</li>" for i in items) + "</ul>" + lists)


def _cost_block(c: CaseResult) -> str:
    if not (c.latency_a or c.latency_b or c.tokens_a or c.tokens_b):
        return ""
    delta = c.latency_b - c.latency_a
    return ("<h4>Latency and tokens</h4><ul class=\"kv\">"
            f"<li>latency A {_num(c.latency_a, 3)}s, B {_num(c.latency_b, 3)}s "
            f"(delta {delta:+.3f}s)</li>"
            f"<li>tokens A {c.tokens_a}, B {c.tokens_b}</li></ul>")


def _case_tbody(report: DiffReport, c: CaseResult, index: int) -> str:
    is_reg = report._is_regression(c)
    da, db, how = _word_diff(c.output_a, c.output_b)
    latency_delta = c.latency_b - c.latency_a
    tags = _tags_of(c)
    # a JSON array, so a tag that contains a separator cannot match another tag
    tag_attr = json.dumps(tags) if tags else ""
    diff_note = {
        "line": '<p class="muted small">Long outputs: changes are matched line by line, '
                "and large changed blocks are highlighted whole.</p>",
        "none": '<p class="muted small">Word diff omitted for long outputs.</p>',
    }.get(how, "")
    fmt_cell = (f'<span class="warn">{_e(str(c.signature_a))} → {_e(str(c.signature_b))}</span>'
                if c.format_changed else '<span class="muted">-</span>')
    ratio_cell = _num(c.length_ratio, 2) + "x" if c.length_ratio is not None else "-"
    attrs = {
        "id": c.case_id, "drift": c.drift_score, "similarity": c.similarity,
        "level": c.drift_level, "levelrank": _LEVEL_RANK.get(c.drift_level, 0),
        "verdict": c.judge_verdict or "", "flip": c.check_flip or "",
        "format": 1 if c.format_changed else 0,
        "ratio": "" if c.length_ratio is None else c.length_ratio,
        "latency": round(latency_delta, 4), "tags": tag_attr,
        "regression": 1 if is_reg else 0, "index": index,
    }
    attr_s = " ".join(f'data-{k}="{_e(str(v))}"' for k, v in attrs.items())
    detail = (
        f'<tr class="detail"><td colspan="9"><div class="detail-body">'
        f'<h4>Input</h4>{_pre(c.input)}'
        f'<div class="diff"><div><h4>A: {_e(report.version_a)}</h4>{_pre(c.output_a, da)}</div>'
        f'<div><h4>B: {_e(report.version_b)}</h4>{_pre(c.output_b, db)}</div></div>'
        + diff_note + _checks_table(c) + _judge_block(c) + _structure_block(c) + _samples_block(c)
        + _cost_block(c)
        + (f'<h4>Tags</h4><p>{_tag_chips(tags)}</p>' if tags else "")
        + "</div></td></tr>"
    )
    row = (
        f'<tr class="row{" reg" if is_reg else ""}" tabindex="0">'
        f'<td class="id"><span class="caret"></span><strong>{_e(c.case_id)}</strong>'
        f'{_tag_chips(tags)}</td>'
        f'<td class="num"><span class="mini"><span class="mini-fill l-{_e(c.drift_level)}"'
        f' style="width:{_clamp_pct(c.drift_score):.1f}%"></span></span>{_pct(c.drift_score)}</td>'
        f'<td class="num">{_pct(c.similarity)}</td>'
        f'<td>{_level_badge(c.drift_level)}</td>'
        f'<td>{_verdict_badge(c.judge_verdict)}</td>'
        f'<td>{_flip_badge(c.check_flip)}</td>'
        f'<td>{fmt_cell}</td>'
        f'<td class="num">{ratio_cell}</td>'
        f'<td class="num">{latency_delta:+.3f}s</td></tr>'
    )
    return f'<tbody class="case" {attr_s}>{row}{detail}</tbody>\n'


def _case_section(report: DiffReport) -> str:
    levels = sorted({c.drift_level for c in report.cases}, key=lambda x: _LEVEL_RANK.get(x, 9))
    verdicts = sorted({c.judge_verdict for c in report.cases if c.judge_verdict})
    tags = sorted({t for c in report.cases for t in _tags_of(c)})

    def options(values: Iterable[str]) -> str:
        return "".join(f'<option value="{_e(v)}">{_e(v)}</option>' for v in values)

    controls = (
        '<div class="controls js-only">'
        '<input id="q" type="search" placeholder="Filter cases by text" '
        'aria-label="Filter by text">'
        f'<select id="f-level" aria-label="Level"><option value="">All levels</option>'
        f'{options(levels)}</select>'
        + (f'<select id="f-verdict" aria-label="Verdict"><option value="">All verdicts</option>'
           f'{options(verdicts)}</select>' if verdicts else "")
        + (f'<select id="f-tag" aria-label="Tag"><option value="">All tags</option>'
           f'{options(tags)}</select>' if tags else "")
        + '<label><input id="f-reg" type="checkbox"> Regressions only</label>'
        f'<span id="f-count" class="muted small">{len(report.cases)} of '
        f'{len(report.cases)}</span>'
        '</div>'
    )
    heads = [
        ("id", "str", "Case"), ("drift", "num", "Drift"), ("similarity", "num", "Similarity"),
        ("levelrank", "num", "Level"), ("verdict", "str", "Judge"), ("flip", "str", "Check flip"),
        ("format", "num", "Format change"), ("ratio", "num", "Length B/A"),
        ("latency", "num", "Latency delta"),
    ]
    thead = "".join(
        f'<th data-key="{k}" data-type="{t}" scope="col"'
        + (' class="num"' if t == "num" and k not in ("levelrank", "format") else "")
        + f'>{_e(label)}</th>' for k, t, label in heads)
    bodies = "".join(_case_tbody(report, c, i) for i, c in enumerate(report.cases))
    if not report.cases:
        bodies = '<tbody><tr><td colspan="9" class="muted">No cases.</td></tr></tbody>'
    return (f'<section><h2>Cases</h2><p class="muted small js-only">Click a header to sort, '
            f'click a row to expand its diff.</p>{controls}'
            f'<div class="table-wrap"><table id="cases"><thead><tr>{thead}</tr></thead>'
            f'{bodies}</table></div></section>\n')


def _footer() -> str:
    return (f'<footer class="muted small">Generated by promptdrift v{_e(__version__)}. '
            f'<a href="{REPO_URL}">github.com/dakshtrehan/promptdrift</a>. '
            'This file loads nothing from the network.</footer>\n')


# ── Page assembly ────────────────────────────────────────────────────────────
_LIGHT = ("--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--card:#f6f8fa;--border:#d0d7de;"
          "--accent:#0969da;--good:#1a7f37;--bad:#cf222e;--warn:#9a6700;--orange:#bc4c00;"
          "--cyan:#0a7ea4;--del:#ffd7d5;--ins:#ccffd8;--delfg:#82071e;--insfg:#044f1e;"
          "--track:#e6edf3;--row:#f6f8fa;--shadow:rgba(0,0,0,.06)")
_DARK = ("--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--card:#161b22;--border:#30363d;"
         "--accent:#58a6ff;--good:#3fb950;--bad:#f85149;--warn:#d29922;--orange:#f0883e;"
         "--cyan:#39c5cf;--del:#5a1e1e;--ins:#1b4721;--delfg:#ffdcd7;--insfg:#aff5b4;"
         "--track:#21262d;--row:#161b22;--shadow:rgba(0,0,0,.4)")

_CSS = f"""
:root{{{_LIGHT};color-scheme:light}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{{_DARK};color-scheme:dark}}}}
:root[data-theme=dark]{{{_DARK};color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:16px}}
h1{{font-size:22px;margin:0}}h2{{font-size:17px;margin:28px 0 10px}}h4{{font-size:13px;margin:14px 0 4px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
a{{color:var(--accent)}}code{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}}
.muted{{color:var(--muted)}}.small{{font-size:13px}}.good{{color:var(--good)}}.bad{{color:var(--bad)}}.warn{{color:var(--warn)}}
.title-row{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
button,select,input[type=search]{{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--border);border-radius:6px;padding:6px 10px}}
button{{cursor:pointer}}
.meta{{display:flex;flex-wrap:wrap;gap:6px 22px;margin:10px 0}}.meta .k{{color:var(--muted);font-size:12px;text-transform:uppercase;margin-right:4px}}
.verdict{{padding:10px 14px;border-radius:8px;border:1px solid var(--border);margin-top:8px;font-size:16px}}
.v-safe{{border-color:var(--good);color:var(--good)}}.v-review{{border-color:var(--warn);color:var(--warn)}}.v-do_not_ship{{border-color:var(--bad);color:var(--bad)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;margin-top:20px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 14px;box-shadow:0 1px 2px var(--shadow)}}
.card .label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.card .value{{font-size:24px;font-weight:600;margin:4px 0}}.card .sub{{font-size:13px;color:var(--muted)}}
.c-good .value{{color:var(--good)}}.c-bad .value{{color:var(--bad)}}.c-warn .value{{color:var(--warn)}}
.ci{{display:grid;grid-template-columns:150px 1fr 220px;gap:12px;align-items:center;margin:8px 0}}
.ci-track{{position:relative;height:18px;background:var(--track);border-radius:4px;overflow:hidden}}
.ci-fill{{position:absolute;left:0;top:0;bottom:0;border-radius:4px}}.f-good{{background:var(--good)}}.f-bad{{background:var(--bad)}}.f-warn{{background:var(--warn)}}
.ci-range{{position:absolute;top:3px;bottom:3px;border-left:2px solid var(--fg);border-right:2px solid var(--fg);background:var(--shadow)}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--border);vertical-align:top}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);background:var(--card);white-space:nowrap}}
td.num,th.num{{text-align:right;white-space:nowrap}}
.table-wrap{{overflow-x:auto}}
#cases th[data-key]{{cursor:default}}.js #cases th[data-key]{{cursor:pointer}}
#cases th[data-dir=asc]::after{{content:" \\25B2"}}#cases th[data-dir=desc]::after{{content:" \\25BC"}}
tbody.case.hidden{{display:none}}
.js tr.row{{cursor:pointer}}tr.row:hover td{{background:var(--row)}}tr.row.reg td.id strong{{color:var(--bad)}}
.caret{{display:none}}.js .caret{{display:inline-block;width:0;height:0;border:5px solid transparent;border-left-color:var(--muted);margin-right:6px;vertical-align:middle}}
.js tbody.case.open .caret{{transform:rotate(90deg)}}
.js tr.detail{{display:none}}.js tbody.case.open tr.detail{{display:table-row}}
tr.detail td{{background:var(--card)}}
.detail-body{{max-width:100%}}
.badge{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid transparent;white-space:nowrap}}
.b-none{{color:var(--good);border-color:var(--good)}}.b-minor{{color:var(--cyan);border-color:var(--cyan)}}.b-moderate{{color:var(--warn);border-color:var(--warn)}}
.b-major{{color:var(--orange);border-color:var(--orange)}}.b-regression{{color:var(--bad);border-color:var(--bad)}}
.b-good{{color:var(--good);border-color:var(--good)}}.b-bad{{color:var(--bad);border-color:var(--bad)}}.b-warn{{color:var(--warn);border-color:var(--warn)}}.b-muted{{color:var(--muted);border-color:var(--border)}}
.tag{{display:inline-block;background:var(--track);color:var(--fg);border-radius:4px;padding:0 6px;font-size:12px;margin-left:6px}}
.mini{{display:inline-block;width:56px;height:7px;background:var(--track);border-radius:3px;margin-right:8px;vertical-align:middle;overflow:hidden}}
.mini-fill{{display:block;height:100%}}.l-none{{background:var(--good)}}.l-minor{{background:var(--cyan)}}.l-moderate{{background:var(--warn)}}.l-major{{background:var(--orange)}}.l-regression{{background:var(--bad)}}
.diff{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
pre.out{{margin:0 0 8px;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font-size:13px;max-height:420px;overflow:auto}}
pre.out.empty{{color:var(--muted);font-style:italic}}
del{{background:var(--del);color:var(--delfg);text-decoration:line-through}}ins{{background:var(--ins);color:var(--insfg);text-decoration:none}}
ul.kv{{margin:0;padding-left:18px}}.reason{{margin:4px 0;white-space:pre-wrap}}
table.checks,table.tags{{width:auto;min-width:50%}}
.controls{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 12px}}.controls input[type=search]{{flex:1;min-width:160px}}
.js-only{{display:none}}.js .js-only{{display:inline-block}}.js .controls.js-only{{display:flex}}.js p.js-only{{display:block}}
footer{{margin:36px 0 12px;border-top:1px solid var(--border);padding-top:12px}}
@media (max-width:720px){{.ci{{grid-template-columns:1fr;gap:4px}}.diff{{grid-template-columns:1fr}}main{{padding:12px}}.card .value{{font-size:20px}}th,td{{padding:6px}}
.detail-body{{position:sticky;left:0;max-width:calc(100vw - 40px)}}.detail-body table{{display:block;overflow-x:auto;min-width:0}}}}
"""  # noqa: E501

_HEAD_JS = ("(function(){var d=document.documentElement;d.className+=' js';"
            "try{var t=localStorage.getItem('promptdrift-theme');"
            "if(t==='dark'||t==='light'){d.setAttribute('data-theme',t);}}catch(e){}})();")

_JS = """
(function(){
var d=document.documentElement,table=document.getElementById('cases');
var btn=document.getElementById('theme');
function current(){var t=d.getAttribute('data-theme');if(t){return t;}
  return (window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';}
if(btn){btn.addEventListener('click',function(){var n=current()==='dark'?'light':'dark';
  d.setAttribute('data-theme',n);try{localStorage.setItem('promptdrift-theme',n);}catch(e){}});}
function bodies(){return Array.prototype.slice.call(table.querySelectorAll('tbody.case'));}
bodies().forEach(function(tb){var row=tb.querySelector('tr.row');
  function toggle(){tb.classList.toggle('open');}
  row.addEventListener('click',function(ev){if(ev.target.tagName!=='A'){toggle();}});
  row.addEventListener('keydown',function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();toggle();}});});
var dirs={};
Array.prototype.forEach.call(table.querySelectorAll('th[data-key]'),function(th){
  th.addEventListener('click',function(){
    var k=th.getAttribute('data-key'),num=th.getAttribute('data-type')==='num';
    var asc=dirs[k]=!dirs[k];
    var rows=bodies();
    rows.sort(function(x,y){var a=x.getAttribute('data-'+k)||'',b=y.getAttribute('data-'+k)||'';
      if(num){a=parseFloat(a);b=parseFloat(b);if(isNaN(a)){a=-Infinity;}if(isNaN(b)){b=-Infinity;}
        if(a===b){return parseInt(x.getAttribute('data-index'),10)-parseInt(y.getAttribute('data-index'),10);}
        return asc?a-b:b-a;}
      var c=a.localeCompare(b);return asc?c:-c;});
    rows.forEach(function(b){table.appendChild(b);});
    Array.prototype.forEach.call(table.querySelectorAll('th[data-key]'),function(o){o.removeAttribute('data-dir');});
    th.setAttribute('data-dir',asc?'asc':'desc');});});
var q=document.getElementById('q'),lv=document.getElementById('f-level'),vd=document.getElementById('f-verdict'),
    tg=document.getElementById('f-tag'),rg=document.getElementById('f-reg'),count=document.getElementById('f-count');
var all=bodies(),total=all.length;
function tagsOf(tb){if(!tb._tags){try{tb._tags=JSON.parse(tb.getAttribute('data-tags')||'[]');}catch(e){tb._tags=[];}}return tb._tags;}
function textOf(tb){if(tb._text===undefined){tb._text=tb.textContent.toLowerCase();}return tb._text;}
function apply(){var text=(q.value||'').toLowerCase(),n=0;
  all.forEach(function(tb){var ok=true;
    if(lv&&lv.value&&tb.getAttribute('data-level')!==lv.value){ok=false;}
    if(vd&&vd.value&&tb.getAttribute('data-verdict')!==vd.value){ok=false;}
    if(tg&&tg.value&&tagsOf(tb).indexOf(tg.value)<0){ok=false;}
    if(rg&&rg.checked&&tb.getAttribute('data-regression')!=='1'){ok=false;}
    if(ok&&text&&textOf(tb).indexOf(text)<0){ok=false;}
    tb.classList.toggle('hidden',!ok);if(ok){n++;}});
  if(count){count.textContent=n+' of '+total;}}
var timer=null;
function later(){if(timer){clearTimeout(timer);}timer=setTimeout(function(){timer=null;apply();},150);}
function now(){if(timer){clearTimeout(timer);timer=null;}apply();}
if(q){q.addEventListener('input',later);q.addEventListener('change',now);}
[lv,vd,tg,rg].forEach(function(el){if(el){el.addEventListener('input',now);el.addEventListener('change',now);}});
})();
"""  # noqa: E501


def render_html(report: DiffReport) -> str:
    """Render ``report`` as one self-contained HTML page (see the module docstring)."""
    status, _ = verdict_for(report)
    title = f"promptdrift: {report.version_a} vs {report.version_b}"
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n"
        f"<script>{_HEAD_JS}</script>\n"
        f"<style>{_CSS}</style>\n</head>\n"
        f'<body data-verdict="{status}">\n<main>\n'
        + _header(report) + _summary_cards(report) + _ci_section(report)
        + _tag_section(report) + _case_section(report) + _footer()
        + f"</main>\n<script>{_JS}</script>\n</body>\n</html>\n"
    )


def save_html(report: DiffReport, path: Union[str, Path]) -> None:
    """Write ``render_html(report)`` to ``path`` as UTF-8.

    Lone surrogates in the text become U+FFFD so the strict UTF-8 write cannot fail.
    """
    from .export import _utf8_safe

    with open(path, "w", encoding="utf-8") as f:
        f.write(_utf8_safe(render_html(report)))
