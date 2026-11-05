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
    assert esc_code("<i>&") == "`&lt;i&gt;&amp;`"


def test_hostile_outputs_cannot_close_details_or_break_tables():
    hostile = "</details>\n<script>alert(1)</script>\n| x | y |\n```\n# heading"
    rep = DiffReport("v1", "v2", "m", "m", 1, [bare_case(
        case_id="</details> | id", output_a=hostile, output_b=hostile + "`",
        judge_verdict="regressed", judge_reasoning="</details> <b>bold</b> | pipe",
        tags=["t|ag", "`tick`"])], n_bootstrap=0)
    md = render_markdown(rep)
    assert "</details>" in md  # exactly one, ours, on its own line at the end of the block
    assert md.count("</details>") == 1 and md.count("<details>") == 1
    assert "<script>" not in md and "</script>" not in md
    assert "<b>" not in md
    # outputs never start a line, so "</details>" and "# heading" cannot open a block
    assert all(not ln.startswith(("&lt;", "<", "#")) for ln in md.splitlines()
               if "alert(1)" in ln)
    # the tag table has exactly 6 cells per row even with a pipe inside a tag
    tag_rows = [ln for ln in md.splitlines() if ln.startswith("| `")]
    assert tag_rows and all(len(re.split(r"(?<!\\)\|", ln)) == 8 for ln in tag_rows)


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
    assert "—" not in md and "–" not in md


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
