"""Tests for the self-contained HTML report. No network, no API keys."""
import argparse
import html.parser
import re

import pytest

from promptdrift import html_report as hr
from promptdrift.cli import _emit, main
from promptdrift.core import CaseResult, DiffReport
from promptdrift.html_report import render_html, save_html, word_diff
from promptdrift.offline import compare_outputs
from promptdrift.reporter import VERDICT_LABEL, print_report, verdict_for

from .conftest import FakeProvider, verdict_json

RECORDS = [
    {"case_id": "capital", "input": "Capital of France?", "expected": "Paris",
     "output_a": "Paris", "output_b": "The capital of France is Paris.",
     "checks": {"regex": "Paris"}, "tags": ["qa", "short"]},
    {"case_id": "percent", "input": "15% of 200?", "expected": "30",
     "output_a": "30", "output_b": "15% of 200 is 3.", "checks": {"regex": r"\b30\b"},
     "tags": ["math"]},
    {"case_id": "extract", "input": "JSON please", "output_a": '{"name": "Ada", "age": 36}',
     "output_b": '```json\n{"name": "Ada", "age": 36}\n```', "checks": {"json_keys": ["name"]},
     "tags": "json"},
    {"case_id": "samples", "input": "reverse", "output_a": ["s[::-1]", "s[::-1]", "rev(s)"],
     "output_b": ["s[::-1]", "rev(s)", "s[::-1]"]},
    {"case_id": "unicode", "input": "hi in Arabic", "output_a": "مرحبا 👋",
     "output_b": "مرحبا بالعالم 👋🌍", "tags": ["i18n"]},
    {"case_id": "empty", "input": "nothing", "output_a": "", "output_b": ""},
    {"case_id": "inject", "input": "<b>bold</b>", "output_a": "<script>alert(1)</script>",
     "output_b": "<img src=x onerror=alert(1)> & done"},
]


def full_report(**kw):
    """A report with every field populated: checks, tags, samples, judge, CIs, latency."""
    judge = FakeProvider([verdict_json("regressed", "B says 3 <not> 30"),
                          verdict_json("regressed", "B says 3 <not> 30"),
                          verdict_json("improved", "nicer"), verdict_json("neutral", "same"),
                          verdict_json("improved", "a"), verdict_json("regressed", "b")])
    rep = compare_outputs([dict(r) for r in RECORDS], version_a="v1", version_b="v2",
                          model_a="m1", model_b="m2", use_judge=True, judge_provider=judge,
                          bootstrap=50, seed=3, **kw)
    for i, c in enumerate(rep.cases):
        c.latency_a, c.latency_b = 0.4 + i / 10, 0.5 + i / 10
        c.tokens_a, c.tokens_b = 10 + i, 20 + i
    rep.cases[-1].judge_verdict = "error"
    return rep


def bare_report(n_bootstrap=0):
    """A report whose optional fields are all None (hand-built CaseResult)."""
    return DiffReport(version_a="old", version_b="new", model_a="x", model_b="y", total_cases=1,
                      n_bootstrap=n_bootstrap,
                      cases=[CaseResult(case_id="only", input="", output_a="a b",
                                        output_b="a c", similarity=0.5, drift_score=0.5,
                                        drift_level="major")])


# ── word diff ─────────────────────────────────────────────────────────────────
def test_word_diff_known_pair():
    a, b = word_diff("the quick brown fox", "the slow brown fox jumps")
    assert a == "the <del>quick</del> brown fox"
    assert b == "the <ins>slow</ins> brown fox <ins>jumps</ins>"


def test_word_diff_escapes_and_keeps_whitespace():
    a, b = word_diff("  <b>x</b>\nline", "<b>y</b>\n\nline")
    assert "<b>" not in a and "&lt;b&gt;x&lt;/b&gt;" in a
    assert a == "  <del>&lt;b&gt;x&lt;/b&gt;</del>\nline"
    assert b == "<ins>&lt;b&gt;y&lt;/b&gt;</ins>\n\nline"
    a, b = word_diff("one two three four", "one 2 3 four")
    assert a == "one <del>two three</del> four" and b == "one <ins>2 3</ins> four"


def test_word_diff_empty_and_identical():
    assert word_diff("", "") == ("", "")
    assert word_diff("same here", "same here") == ("same here", "same here")
    assert word_diff("", "new") == ("", "<ins>new</ins>")
    assert word_diff("old", "") == ("<del>old</del>", "")


# ── rendering ─────────────────────────────────────────────────────────────────
class _Counter(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.min_depth = 0
        self.tags = []

    def handle_starttag(self, tag, attrs):
        if tag not in ("meta", "input", "br", "img"):
            self.depth += 1
        self.tags.append(tag)

    def handle_endtag(self, tag):
        self.depth -= 1
        self.min_depth = min(self.min_depth, self.depth)


def _parse(page):
    p = _Counter()
    p.feed(page)
    p.close()
    return p


def test_render_full_report_has_every_section():
    rep = full_report()
    page = render_html(rep)
    assert page.startswith("<!DOCTYPE html>") and '<meta charset="utf-8">' in page
    assert "<title>promptdrift: v1 vs v2</title>" in page
    assert "(m1)" in page and "(m2)" in page and "offline (stored outputs)" in page
    status, reason = verdict_for(rep)
    assert status == "do_not_ship" and f'data-verdict="{status}"' in page
    assert VERDICT_LABEL[status] in page and reason in page
    # summary cards
    for label in ("Average drift", "Regression rate", "Structural changes", "Check flips",
                  "Judge inconsistency", "Stability"):
        assert label in page
    # CI bars with range markers
    assert page.count('class="ci-range"') >= 2 and "95% CI" in page
    assert "Average excess drift" in page
    # tag table
    assert "By tag" in page and 'data-tags="|qa|short|"' in page and '<span class="tag">json</span>' in page
    assert '<select id="f-tag"' in page and '<option value="i18n">' in page
    # case table rows and details
    assert page.count('<tbody class="case"') == len(RECORDS)
    assert 'data-regression="1"' in page
    assert "<del>" in page and "<ins>" in page
    assert "Checks</h4>" in page and "pass_to_fail" in page.replace("pass to fail", "pass_to_fail")
    assert "B says 3 &lt;not&gt; 30" in page
    assert "A then B: regressed" in page
    assert "samples per side 3" in page and "All 3 samples of A" in page
    assert "Latency and tokens" in page and "tokens A 10, B 20" in page
    assert "signature A <code>" in page and "(changed)" in page
    assert '<option value="error">' in page
    # balanced markup
    parsed = _parse(page)
    assert parsed.depth == 0 and parsed.min_depth == 0
    assert "pre" in parsed.tags and "del" in parsed.tags


def test_render_escapes_prompt_outputs():
    page = render_html(full_report())
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(1)&gt; &amp; done" in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page
    # the only scripts are the two inline ones we wrote
    assert page.count("<script>") == 2


def test_render_no_external_requests():
    page = render_html(full_report())
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", page))
    assert urls == {hr.REPO_URL}
    assert re.search(r"<(script|link|img|iframe)[^>]*\s(src|href)=", page) is None
    assert "@import" not in page and "url(" not in page
    assert "<link" not in page


def test_render_unicode_and_empty_outputs():
    page = render_html(full_report())
    assert "مرحبا بالعالم 👋🌍" in page or "مرحبا" in page
    assert 'dir="auto"' in page
    assert page.count('class="out empty"') >= 2 and "(empty)" in page


def test_render_bare_report_all_none():
    rep = bare_report()
    page = render_html(rep)
    assert "Bootstrap confidence intervals disabled" in page
    assert "By tag" not in page and 'id="f-tag"' not in page and 'id="f-verdict"' not in page
    assert "Structural changes" not in page and "Check flips" not in page
    assert "Stability" not in page and "Latency and tokens" not in page
    assert "Checks</h4>" not in page and "Judge</h4>" not in page
    assert '<span class="muted">-</span>' in page  # verdict / flip / format cells
    assert "<del>b</del>" in page and "<ins>c</ins>" in page
    parsed = _parse(page)
    assert parsed.depth == 0 and parsed.min_depth == 0


def test_render_bare_report_with_ci():
    page = render_html(bare_report(n_bootstrap=20))
    assert 'class="ci-range"' in page and "over 20 resamples" in page


def test_render_empty_report():
    rep = DiffReport(version_a="a", version_b="b", model_a="m", model_b="m", total_cases=0)
    page = render_html(rep)
    assert "No cases." in page and "SAFE TO SHIP" in page


def test_render_hand_built_fields():
    c = CaseResult(case_id="x", input="i", output_a="a", output_b="b", similarity=0.9,
                   drift_score=0.1, drift_level="minor", judge_verdict="inconsistent",
                   judge_reasoning="flip", judge_verdict_ab="improved",
                   judge_verdict_ba="regressed", judge_consistent=False,
                   signature_a="text", signature_b="json:object", format_changed=True,
                   json_valid_a=False, json_valid_b=True, json_validity_changed=True,
                   length_ratio=2.5, check_results_a=[{"type": "regex", "passed": True,
                                                       "detail": "x"}],
                   check_results_b=None, check_flip="fail_to_pass", checks_passed_a=True,
                   checks_passed_b=False, n_samples=2, excess_drift=0.2, noise_floor=0.0,
                   within_drift_a=0.0, within_drift_b=None, samples_a=None, samples_b=["b"],
                   tags=["t one"])
    rep = DiffReport(version_a="a", version_b="b", model_a="m", model_b="m", total_cases=1,
                     cases=[c], n_bootstrap=0)
    page = render_html(rep)
    assert "text → json:object" in page and "inconsistent" in page
    assert "fail to pass" in page and 'data-tags="|t one|"' in page
    assert "All 1 samples of B" in page and "All 1 samples of A" not in page
    assert "2.5x" in page and "within-version drift A 0.0%, B n/a" in page
    assert verdict_for(rep)[0] == "review" and 'data-verdict="review"' in page


def test_render_review_verdict_for_drift_only():
    rep = bare_report()
    rep.cases[0].drift_level = "moderate"
    assert verdict_for(rep) == ("review", "semantic drift, no regressions")
    assert "REVIEW" in render_html(rep)


def test_verdict_helper_matches_print_report(capsys):
    rep = full_report()
    print_report(rep, show_cases=False)
    out = capsys.readouterr().out
    status, reason = verdict_for(rep)
    assert f"DO NOT SHIP: {reason}" in out
    safe = DiffReport(version_a="a", version_b="b", model_a="m", model_b="m", total_cases=0)
    assert verdict_for(safe) == ("safe", "no regressions, minimal drift")
    print_report(safe, show_cases=False)
    assert "SAFE TO SHIP: no regressions, minimal drift" in capsys.readouterr().out


def test_save_html_utf8(tmp_path):
    p = tmp_path / "r.html"
    save_html(full_report(), p)
    data = p.read_bytes()
    assert data.startswith(b"<!DOCTYPE html>") and "👋".encode() in data
    assert len(data) < 200_000


# ── tags plumbing ─────────────────────────────────────────────────────────────
def test_tags_normalised_from_records():
    rep = full_report()
    by_id = {c.case_id: c for c in rep.cases}
    assert by_id["capital"].tags == ["qa", "short"]
    assert by_id["extract"].tags == ["json"]  # a bare string becomes a one-item list
    assert by_id["samples"].tags is None
    assert rep.to_dict()["cases"][0]["tags"] == ["qa", "short"]


def test_tags_default_none_keeps_dataclass_order():
    import dataclasses
    names = [f.name for f in dataclasses.fields(CaseResult)]
    assert names[-1] == "tags" and names[-2] == "judge_consistent"
    assert CaseResult("i", "x", "a", "b", 1.0, 0.0, "none").tags is None


# ── CLI ───────────────────────────────────────────────────────────────────────
def test_cli_diff_writes_html_csv_junit(tmp_path, capsys):
    html_p, csv_p, junit_p = (tmp_path / n for n in ("r.html", "r.csv", "r.xml"))
    main(["diff", "examples/outputs.jsonl", "--summary", "--bootstrap", "20",
          "--html", str(html_p), "--csv", str(csv_p), "--junit", str(junit_p)])
    out = capsys.readouterr().out
    assert "HTML report saved" in out and "CSV saved" in out and "JUnit XML saved" in out
    page = html_p.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in page and page.count('<tbody class="case"') == 5
    assert csv_p.read_text(encoding="utf-8").startswith("case_id,tags,")
    assert junit_p.read_text(encoding="utf-8").startswith('<?xml version="1.0"')


def test_cli_help_lists_export_flags(capsys):
    with pytest.raises(SystemExit):
        main(["diff", "--help"])
    out = capsys.readouterr().out
    assert "--html FILE" in out and "--csv FILE" in out and "--junit FILE" in out
    with pytest.raises(SystemExit):
        main(["compare", "--help"])
    assert "--junit FILE" in capsys.readouterr().out


def test_emit_tolerates_old_namespaces(capsys):
    """Callers built before --html existed may pass a Namespace without the new attributes."""
    args = argparse.Namespace(json=True, output=None, summary=True, show_outputs=False,
                              fail_on_regression=False)
    _emit(bare_report(), args)
    assert '"version_a": "old"' in capsys.readouterr().out


# ── small helpers ─────────────────────────────────────────────────────────────
def test_num_and_pct_helpers():
    assert hr._num(None) == "n/a" and hr._num(True) == "yes" and hr._num(False) == "no"
    assert hr._num(2.50) == "2.5" and hr._num(2.0, 2) == "2" and hr._num(2.6, 0) == "3"
    assert hr._num(7) == "7" and hr._pct(None) == "n/a" and hr._pct(0.1234) == "12.3%"


def test_load_cases_tags_validation(tmp_path):
    from promptdrift.compare import load_cases

    good = tmp_path / "good.json"
    good.write_text('[{"input": "x", "tags": ["a"]}, {"input": "y", "tags": "b"}, {"input": "z"}]')
    cases = load_cases(good)
    assert cases[0]["tags"] == ["a"] and cases[1]["tags"] == "b" and "tags" not in cases[2]
    bad = tmp_path / "bad.json"
    bad.write_text('[{"input": "x", "tags": 5}]')
    with pytest.raises(ValueError, match="tags"):
        load_cases(bad)
