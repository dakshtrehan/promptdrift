"""Tests for the GitHub flavoured Markdown renderer. No network, no API keys."""
import json
import re

from promptdrift import render_markdown
from promptdrift.core import CaseResult, DiffReport
from promptdrift.markdown import esc_code, esc_text, report_from_dict
from promptdrift.offline import compare_outputs
from promptdrift.reporter import verdict_for

from .conftest import FakeProvider, verdict_json

RECORDS = [
    {"case_id": "same", "input": "q", "output_a": "Paris", "output_b": "Paris",
     "checks": {"regex": "Paris"}, "tags": ["qa", "short"]},
    {"case_id": "flip", "input": "15%", "output_a": "30", "output_b": "15% of 200 is 3.",
     "checks": {"regex": r"\b30\b"}},
    {"case_id": "multi\nline | id", "input": "<x>&</x>", "output_a": ["a b", "a c"],
     "output_b": ["x y", "x z"], "tags": ["edge"]},
    {"case_id": "fmt", "input": "list", "output_a": "a, b", "output_b": "- a\n- b\n- c"},
]


def report(use_judge=False, bootstrap=30):
    judge = FakeProvider([verdict_json("regressed", "worse <b>& bad</b>"),
                          verdict_json("improved", "worse <b>& bad</b>")] * 5)
    return compare_outputs([dict(r) for r in RECORDS], version_a="v1", version_b="v2",
                           bootstrap=bootstrap, seed=1, use_judge=use_judge,
                           judge_provider=judge)


def bare_case(**kw):
    base = dict(case_id="c", input="i", output_a="a", output_b="b", similarity=0.5,
                drift_score=0.5, drift_level="major")
    base.update(kw)
    return CaseResult(**base)


# ── Escaping ──────────────────────────────────────────────────────────────────
def test_esc_text_neutralises_markup_and_pipes():
    assert esc_text("a <b>&</b> | *x* _y_ [z] `q`\nnext") == \
        "a &lt;b&gt;&amp;&lt;/b&gt; \\| \\*x\\* \\_y\\_ \\[z\\] \\`q\\` next"


def test_esc_code_handles_backticks_newlines_and_limits():
    assert esc_code("plain") == "`plain`"
    assert esc_code("") == "(empty)"
    assert esc_code("a\n\nb   c") == "`a b c`"
    assert esc_code("x`y") == "``x`y``"
    assert esc_code("`lead") == "`` `lead ``"
    assert esc_code("tail``") == "``` tail`` ```"
    assert esc_code("x" * 300, limit=10) == "`xxxxxxx...`"
    assert esc_code("a|b", in_table=True) == "`a\\|b`"
    assert esc_code("a|b") == "`a|b`"
    # code spans are shown verbatim, so no entities or backslashes are added
    assert esc_code("<i>&") == "`<i>&`"
    assert esc_code('{"k": [1, 2]}') == '`{"k": [1, 2]}`'
    assert esc_code("snake_case *x*") == "`snake_case *x*`"
    # in a table a pipe always ends up behind an odd number of backslashes
    assert esc_code("a\\|b", in_table=True) == "`a\\\\\\|b`"
    assert esc_code("a\\\\|b", in_table=True) == "`a\\\\\\|b`"


def _outside_code_spans(line):
    """The parts of a Markdown line that are not inside a code span (CommonMark rules)."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            out.append(line[i:i + 2])
            i += 2
            continue
        if ch != "`":
            out.append(ch)
            i += 1
            continue
        j = i
        while j < n and line[j] == "`":
            j += 1
        fence = line[i:j]
        k = j
        close = -1
        while k < n:
            if line[k] == "`":
                m = k
                while m < n and line[m] == "`":
                    m += 1
                if m - k == len(fence):
                    close = k
                    break
                k = m
            else:
                k += 1
        if close < 0:
            out.append(fence)
            i = j
        else:
            out.append("CODE")
            i = close + len(fence)
    return "".join(out)


def test_hostile_outputs_cannot_close_details_or_break_tables():
    hostile = "</details>\n<script>alert(1)</script>\n| x | y |\n```\n# heading"
    rep = DiffReport("v1", "v2", "m", "m", 1, [bare_case(
        case_id="</details> | id", output_a=hostile, output_b=hostile + "`",
        judge_verdict="regressed", judge_reasoning="</details> <b>bold</b> | pipe",
        tags=["t|ag", "`tick`"])], n_bootstrap=0)
    md = render_markdown(rep)
    # Hostile text may appear verbatim, but only inside code spans, which
    # GitHub never parses as HTML. Outside them there is exactly one
    # <details> block (ours) and no injected tag.
    outside = "\n".join(_outside_code_spans(ln) for ln in md.splitlines())
    assert outside.count("</details>") == 1 and outside.count("<details>") == 1
    assert md.splitlines().count("</details>") == 1  # ours, on its own line
    assert "<script>" not in outside and "</script>" not in outside
    assert "<b>" not in outside
    # outputs never start a line, so "</details>" and "# heading" cannot open a block
    assert all(not ln.startswith(("&lt;", "<", "#", "`")) for ln in md.splitlines()
               if "alert(1)" in ln)
    # the tag table has exactly 6 cells per row even with a pipe inside a tag
    tag_rows = [ln for ln in md.splitlines() if ln.startswith("| `")]
    assert tag_rows and all(len(re.split(r"(?<!\\)\|", ln)) == 8 for ln in tag_rows)


def test_hostile_outputs_parse_as_code_with_a_commonmark_parser():
    """Round trip through a real CommonMark + table parser (markdown-it-py)."""
    import pytest

    markdown_it = pytest.importorskip("markdown_it")
    hostile = "</details>\n<script>alert(1)</script>\n| x | y |\n```\n# heading & <b>"
    rep = DiffReport("v1_base", "v2`x", "m", "m", 1, [bare_case(
        case_id="</details> | id", output_a=hostile, output_b='{"k": "<v>&"}',
        judge_verdict="regressed", judge_reasoning="</details> <b>bold</b> | pipe",
        tags=["t|ag", "`tick`", "a\\|b", "x<y&z"])], n_bootstrap=0)
    md = render_markdown(rep)
    tokens = markdown_it.MarkdownIt("commonmark").enable("table").parse(md)

    def walk(toks):
        for t in toks:
            yield t
            yield from walk(t.children or [])

    every = list(walk(tokens))
    raw_html = {line.strip() for t in every if t.type in ("html_inline", "html_block")
                for line in t.content.splitlines() if line.strip()}
    # the only raw HTML the parser sees is the module's own markup
    assert raw_html == {"<details>", "<summary>Regressions (1)</summary>", "</details>",
                        "<sub>", "</sub>"}, raw_html
    code = [t.content for t in every if t.type == "code_inline"]
    # outputs, versions and tags come back exactly as written (collapsed to one line)
    assert " ".join(hostile.split()) in code
    assert '{"k": "<v>&"}' in code
    assert "v1_base" in code and "v2`x" in code
    assert "t|ag" in code and "`tick`" in code and "x<y&z" in code
    # the summary table keeps 2 cells per row and the tag table 6, pipes or not
    tables = _table_cells(tokens)
    assert [set(t) for t in tables] == [{2}, {6}], tables
    assert len(tables[1]) == 1 + 4  # header plus one row per tag


def _table_cells(tokens):
    """Cells per row, grouped by table, from a markdown-it token stream."""
    tables, rows, count = [], None, 0
    for t in tokens:
        if t.type == "table_open":
            rows = []
        elif t.type == "tr_open":
            count = 0
        elif t.type in ("td_open", "th_open"):
            count += 1
        elif t.type == "tr_close":
            rows.append(count)
        elif t.type == "table_close":
            tables.append(rows)
    return tables


def test_version_names_render_without_backslashes():
    rep = DiffReport("v1_base", "v2`x", "gpt-4o_mini", "m", 0, [], n_bootstrap=0)
    line = render_markdown(rep).splitlines()[4]
    assert line.startswith("A `v1_base` (gpt-4o\\_mini) vs B ``v2`x`` (m)"), line


def test_lone_surrogate_is_replaced_so_the_summary_can_be_written(tmp_path):
    rep = DiffReport("v1", "v2", "m", "m", 1, [bare_case(output_b="Thanks! \ud83d")],
                     n_bootstrap=0)
    md = render_markdown(rep)
    assert "\ud83d" not in md and "Thanks! \ufffd" in md
    (tmp_path / "s.md").write_text(md, encoding="utf-8")


# ── Structure ─────────────────────────────────────────────────────────────────
def test_verdict_summary_tags_and_cases():
    rep = report()
    md = render_markdown(rep, title="My <title>", max_cases=25,
                         artifact_url="https://example.invalid/run/1",
                         marker="<!-- promptdrift-report -->")
    status, reason = verdict_for(rep)
    assert md.startswith("<!-- promptdrift-report -->\n\n## My &lt;title&gt;\n")
    assert status == "do_not_ship" and "**DO NOT SHIP**: 1 regression(s) detected" in md
    assert "A `v1` (stored) vs B `v2` (stored), 4 case(s), offline mode" in md
    assert "| Avg drift |" in md and "95% CI" in md
    assert "| Regressions | 1 |" in md and "| Format changes | 1 |" in md
    assert "| Check flips | 1 pass to fail, 0 fail to pass" in md
    assert "| Stability | n=2/side" in md
    assert "**By tag**" in md and "| `edge` | 1 |" in md and "| `qa` | 1 |" in md
    assert "<summary>Regressions (1) and notable cases (2)</summary>" in md
    assert "**flip**" in md and "why: check pass\\_to\\_fail (regex)" in md
    assert "**multi line \\| id**" in md  # newline collapsed, pipe escaped
    assert "- A: `a b`" in md and "- B: `x y`" in md
    assert "[full HTML report in the run artifacts](https://example.invalid/run/1)" in md
    assert "| Judge |" not in md
    assert chr(0x2014) not in md and chr(0x2013) not in md


def test_judge_rows_and_reasoning_are_escaped():
    rep = report(use_judge=True)
    md = render_markdown(rep)
    assert "| Judge |" in md and "judged" in md
    assert "<b>" not in md and "&lt;b&gt;&amp; bad" in md


def test_safe_and_review_verdicts():
    same = DiffReport("v1", "v2", "m", "m", 1, [bare_case(similarity=1.0, drift_score=0.0,
                                                          drift_level="none")], n_bootstrap=0)
    md = render_markdown(same)
    assert "✅ **SAFE TO SHIP**" in md and "<details>" not in md and "**By tag**" not in md
    drifted = DiffReport("v1", "v2", "m", "m", 1, [bare_case()], n_bootstrap=0)
    md = render_markdown(drifted)
    assert "⚠️ **REVIEW**" in md and "<summary>Notable cases</summary>" in md
    assert "live mode" in md


def test_failures_block_and_max_cases_truncation():
    cases = [bare_case(case_id=f"c{i}", judge_verdict="regressed") for i in range(6)]
    cases += [bare_case(case_id=f"n{i}", format_changed=True) for i in range(3)]
    rep = DiffReport("v1", "v2", "m", "m", 9, cases, n_bootstrap=0)
    md = render_markdown(rep, max_cases=4, failures=["avg drift 50.0% exceeds max-drift 10.0%"])
    assert "**Threshold failures**\n\n- ❌ avg drift 50.0% exceeds max-drift 10.0%" in md
    assert "**c3**" in md and "**c4**" not in md and "**n0**" not in md
    assert "... and 5 more case(s) in the full report." in md
    assert "<summary>Regressions (6) and notable cases (3)</summary>" in md
    md0 = render_markdown(rep, max_cases=0)
    assert "... and 9 more case(s)" in md0


def test_notable_flags_cover_each_kind():
    cases = [
        bare_case(case_id="imp", drift_level="minor", drift_score=0.1, judge_verdict="improved",
                  judge_reasoning="better"),
        bare_case(case_id="inc", drift_level="minor", drift_score=0.1,
                  judge_verdict="inconsistent", judge_reasoning="order"),
        bare_case(case_id="json", drift_level="minor", drift_score=0.1, json_valid_a=True,
                  json_valid_b=False),
        bare_case(case_id="f2p", drift_level="minor", drift_score=0.1, check_flip="fail_to_pass",
                  check_results_a=[{"type": "regex", "passed": False}]),
        bare_case(case_id="stab", n_samples=3, noise_floor=0.2, excess_drift=0.3),
        bare_case(case_id="quiet", drift_level="none", drift_score=0.0, judge_verdict="neutral"),
    ]
    rep = DiffReport("v1", "v2", "m", "m", 6, cases, n_bootstrap=0)
    md = render_markdown(rep)
    assert "judge: better" in md and "judge: order" in md
    assert "JSON broken" in md and "check fail to pass: regex" in md
    assert "n=3 noise 20.0% excess +30.0%" in md
    assert "**quiet**" not in md
    assert "| JSON broken | 1 |" in md


# ── Dict input ────────────────────────────────────────────────────────────────
def test_dict_input_renders_the_same_summary_as_the_report():
    rep = report()
    data = json.loads(rep.to_json())
    md_rep = render_markdown(rep)
    md_dict = render_markdown(data)
    assert md_rep == md_dict  # stored CIs are reused, so nothing churns


def test_report_from_dict_ignores_unknown_keys_and_fills_defaults():
    data = {"cases": [{"case_id": "c", "input": "i", "output_a": "a", "output_b": "b",
                       "similarity": 1.0, "drift_score": 0.0, "drift_level": "none",
                       "future_field": 1}]}
    rep = report_from_dict(data)
    assert (rep.version_a, rep.version_b, rep.total_cases, rep.n_bootstrap) == ("A", "B", 1, 0)
    assert rep.cases[0].case_id == "c" and rep.confidence_intervals() is None
    md = render_markdown(data)
    assert "SAFE TO SHIP" in md and "CI" not in md.split("| Avg drift |")[1].split("|")[0]
    assert render_markdown({}).count("| Regressions | 0 |") == 1


def test_markdown_is_deterministic():
    rep = report()
    assert render_markdown(rep) == render_markdown(rep)
