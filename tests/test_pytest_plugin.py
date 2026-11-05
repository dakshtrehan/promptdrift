"""Tests for the pytest plugin (``pytest --promptdrift``) using pytester.

Each test runs an inner pytest session in-process on a temporary directory.
The plugin is normally loaded through the ``pytest11`` entry point; when the
package is not installed (so the entry point is missing) the inner runs load
it explicitly with ``-p``.
"""
import json
import sys
import textwrap
import xml.etree.ElementTree as ET
from importlib import metadata

import pytest

from promptdrift.core import CaseResult, DiffReport
from promptdrift.pytest_plugin import _numbered
from promptdrift.testing import assert_no_regressions, describe_case

pytest_plugins = ["pytester"]


def _plugin_installed() -> bool:
    eps = metadata.entry_points()
    group = eps.select(group="pytest11") if hasattr(eps, "select") else eps.get("pytest11", [])
    return any(ep.name == "promptdrift" for ep in group)


EXTRA = [] if _plugin_installed() else ["-p", "_promptdrift_pytest"]

ROWS = [
    {"case_id": "good", "input": "q", "output_a": "Paris is the capital.",
     "output_b": "Paris is the capital.", "checks": {"regex": "Paris"}},
    {"case_id": "bad", "input": "q", "output_a": "The answer is 30.",
     "output_b": "The answer is 3.", "checks": {"regex": "\\b30\\b"}},
    {"case_id": "drifted", "input": "q", "output_a": "apple banana cherry",
     "output_b": "car train plane"},
]


def _write(pytester, rows=ROWS, name="out.jsonl"):
    p = pytester.path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return name


def _run(pytester, *args):
    return pytester.runpytest(*EXTRA, "-p", "no:cacheprovider", *args)


# ── inert without the option ───────────────────────────────────────────────────

def test_inert_without_option(pytester):
    _write(pytester)
    pytester.makepyfile(textwrap.dedent("""
        def test_fixture_is_none(promptdrift_report):
            assert promptdrift_report is None
    """))
    result = _run(pytester, "-v")
    result.assert_outcomes(passed=1)
    assert "promptdrift::" not in result.stdout.str()
    assert "error" not in result.stderr.str().lower()


def test_help_lists_options(pytester):
    result = _run(pytester, "--help")
    out = result.stdout.str()
    for opt in ("--promptdrift", "--promptdrift-max-drift", "--promptdrift-max-regressions",
                "--promptdrift-checks", "--promptdrift-json", "--promptdrift-html"):
        assert opt in out


def test_explicit_plugin_flag_still_works(pytester):
    """-p promptdrift.pytest_plugin next to the entry point must not double-register."""
    f = _write(pytester, [ROWS[0]])
    result = _run(pytester, "-p", "promptdrift.pytest_plugin", "--promptdrift", f)
    result.assert_outcomes(passed=2)


def test_option_in_addopts_env_is_seen(pytester, monkeypatch):
    f = _write(pytester, [ROWS[0]])
    monkeypatch.setenv("PYTEST_ADDOPTS", f"--promptdrift {f}")
    result = _run(pytester)
    result.assert_outcomes(passed=2)


# ── collection and outcomes ────────────────────────────────────────────────────

def test_collects_one_item_per_case_plus_summary(pytester):
    f = _write(pytester)
    result = _run(pytester, "--promptdrift", f, "-v")
    result.assert_outcomes(passed=2, failed=2)
    out = result.stdout.str()
    assert f"promptdrift::{f}::good PASSED" in out
    assert f"promptdrift::{f}::drifted PASSED" in out
    assert f"promptdrift::{f}::bad FAILED" in out
    assert f"promptdrift::{f}::summary FAILED" in out
    # readable failure message
    assert "promptdrift regression in bad (A vs B)" in out
    assert "reason: check pass_to_fail (regex)" in out
    assert "flip:   pass_to_fail" in out
    assert "A: The answer is 30." in out and "B: The answer is 3." in out
    assert "1 regression(s) exceed max-regressions 0" in out


def test_no_regressions_all_pass(pytester):
    f = _write(pytester, [ROWS[0], ROWS[2]])
    result = _run(pytester, "--promptdrift", f)
    result.assert_outcomes(passed=3)


def test_max_regressions_and_max_drift(pytester):
    f = _write(pytester)
    result = _run(pytester, "--promptdrift", f, "--promptdrift-max-regressions", "1")
    result.assert_outcomes(passed=3, failed=1)       # only the case fails, summary passes

    result = _run(pytester, "--promptdrift", f, "--promptdrift-max-regressions", "1",
                  "--promptdrift-max-drift", "0.01")
    result.assert_outcomes(passed=2, failed=2)
    assert "exceeds max-drift 1.0%" in result.stdout.str()


def test_repeated_option_collects_each_file(pytester):
    a = _write(pytester, [ROWS[0]], "a.jsonl")
    b = _write(pytester, [ROWS[1]], "b.jsonl")
    result = _run(pytester, "--promptdrift", a, "--promptdrift", b, "-v")
    result.assert_outcomes(passed=2, failed=2)
    out = result.stdout.str()
    assert "promptdrift::a.jsonl::good PASSED" in out
    assert "promptdrift::b.jsonl::bad FAILED" in out


def test_default_checks_file(pytester):
    rows = [{"case_id": "c1", "output_a": "code 42", "output_b": "code forty-two"}]
    f = _write(pytester, rows)
    (pytester.path / "checks.json").write_text(json.dumps({"regex": "\\d+"}))
    result = _run(pytester, "--promptdrift", f, "--promptdrift-checks", "checks.json")
    result.assert_outcomes(failed=2)
    assert "check pass_to_fail (regex)" in result.stdout.str()

    (pytester.path / "checks.yaml").write_text("regex: '\\d+'\n")
    result = _run(pytester, "--promptdrift", f, "--promptdrift-checks", "checks.yaml")
    result.assert_outcomes(failed=2)


def test_marker_selects_generated_items(pytester):
    f = _write(pytester, [ROWS[0]])
    pytester.makepyfile("def test_plain(): pass")
    result = _run(pytester, "--promptdrift", f, "-m", "promptdrift", "-v")
    result.assert_outcomes(passed=2, deselected=1)
    result = _run(pytester, "--promptdrift", f, "-m", "not promptdrift", "--strict-markers")
    result.assert_outcomes(passed=1, deselected=2)


def test_fixture_gives_first_report(pytester):
    f = _write(pytester, [ROWS[0], ROWS[2]])
    pytester.makepyfile(textwrap.dedent("""
        from promptdrift import DiffReport
        from promptdrift.testing import assert_no_regressions

        def test_report(promptdrift_report):
            assert isinstance(promptdrift_report, DiffReport)
            assert promptdrift_report.total_cases == 2
            assert_no_regressions(promptdrift_report)
    """))
    result = _run(pytester, "--promptdrift", f)
    result.assert_outcomes(passed=4)


# ── junit and saved reports ────────────────────────────────────────────────────

def test_junit_xml_shows_cases_and_failures(pytester):
    f = _write(pytester)
    xml = pytester.path / "junit.xml"
    result = _run(pytester, "--promptdrift", f, "--junit-xml", str(xml))
    result.assert_outcomes(passed=2, failed=2)
    root = ET.parse(xml).getroot()
    cases = root.iter("testcase")
    by_name = {tc.get("name"): tc for tc in cases}
    assert set(by_name) == {"good", "bad", "drifted", "summary"}
    assert all(tc.get("classname") == "promptdrift.out.jsonl" for tc in by_name.values())
    assert by_name["bad"].find("failure") is not None
    assert by_name["summary"].find("failure") is not None
    assert by_name["good"].find("failure") is None
    assert "check pass_to_fail" in by_name["bad"].find("failure").get("message", "") + \
        (by_name["bad"].find("failure").text or "")


def test_saves_json_and_html(pytester):
    f = _write(pytester, [ROWS[0]])
    result = _run(pytester, "--promptdrift", f, "--promptdrift-json", "report.json",
                  "--promptdrift-html", "report.html")
    result.assert_outcomes(passed=2)
    data = json.loads((pytester.path / "report.json").read_text())
    assert data["total_cases"] == 1 and data["mode"] == "offline"
    html = (pytester.path / "report.html").read_text()
    assert "<html" in html.lower() and "good" in html


def test_numbered_outputs_for_several_files(pytester):
    a = _write(pytester, [ROWS[0]], "a.jsonl")
    b = _write(pytester, [ROWS[2]], "b.jsonl")
    result = _run(pytester, "--promptdrift", a, "--promptdrift", b,
                  "--promptdrift-json", "out/report.json")
    result.assert_outcomes(passed=4)
    assert (pytester.path / "out" / "report-1.json").exists()
    assert (pytester.path / "out" / "report-2.json").exists()
    assert not (pytester.path / "out" / "report.json").exists()


def test_numbered_helper():
    assert _numbered("r.json", 1, 1) == "r.json"
    assert _numbered("dir/r.json", 2, 3) == "dir/r-2.json"


def test_html_skipped_when_module_missing(pytester, monkeypatch):
    f = _write(pytester, [ROWS[0]])
    monkeypatch.setitem(sys.modules, "promptdrift.html_report", None)
    result = _run(pytester, "--promptdrift", f, "--promptdrift-html", "report.html")
    # The warning count is not asserted: the in-process run also emits pytest's
    # "module already imported so cannot be rewritten" warning for the plugin
    # when an earlier test imported it, which depends on test order.
    result.assert_outcomes(passed=2)
    assert "--promptdrift-html ignored" in result.stdout.str()
    assert not (pytester.path / "report.html").exists()


def test_unexpected_exception_uses_default_failure_repr(pytester):
    f = _write(pytester, [ROWS[0]])
    rec = pytester.inline_run(*EXTRA, "-p", "no:cacheprovider", "--promptdrift", f,
                              "--collect-only")
    items = rec.getcall("pytest_sessionfinish").session.items
    assert [i.name for i in items] == ["good", "summary"]
    with pytest.raises(ValueError) as excinfo:
        raise ValueError("unexpected")
    for item in items:
        rendered = str(item.repr_failure(excinfo))
        assert "ValueError" in rendered and "unexpected" in rendered
    assert items[0].reportinfo()[2] == "promptdrift case good"
    assert items[1].reportinfo()[2] == "promptdrift summary"


# ── errors ────────────────────────────────────────────────────────────────────

def test_missing_file_is_a_usage_error(pytester):
    result = _run(pytester, "--promptdrift", "nope.jsonl")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "file not found: nope.jsonl" in result.stderr.str()


def test_invalid_record_is_a_usage_error(pytester):
    f = _write(pytester, [{"case_id": "x", "output_a": "only one side"}])
    result = _run(pytester, "--promptdrift", f)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "missing 'output_b'" in result.stderr.str()


# ── promptdrift.testing ───────────────────────────────────────────────────────

def _case(cid, a, b, **kw):
    base = dict(case_id=cid, input="q", output_a=a, output_b=b, similarity=0.5,
                drift_score=0.5, drift_level="moderate")
    base.update(kw)
    return CaseResult(**base)


def _report(*cases):
    return DiffReport("v1", "v2", "m", "m", len(cases), list(cases), mode="offline",
                      n_bootstrap=0)


def test_assert_no_regressions_passes_on_clean_report():
    assert_no_regressions(_report(_case("ok", "a", "a")))
    # every threshold disabled never raises, whatever the report holds
    assert_no_regressions(_report(_case("bad", "a", "b", judge_verdict="regressed")),
                          max_regressions=None)


def test_assert_no_regressions_message_lists_cases():
    bad = _case("bad", "The answer is 30, final.", "No idea " * 40,
                judge_verdict="regressed", judge_reasoning="lost the number",
                checks_passed_a=True, checks_passed_b=False, check_flip="pass_to_fail",
                check_results_b=[{"type": "regex", "passed": False}],
                format_changed=True, signature_a="text", signature_b="list")
    report = _report(_case("ok", "a", "a", drift_score=0.0, drift_level="none"), bad)
    with pytest.raises(AssertionError) as e:
        assert_no_regressions(report, max_drift=0.1, max_format_changes=0, max_flips=0)
    msg = str(e.value)
    assert msg.startswith("promptdrift: v1 vs v2 failed 4 threshold(s)")
    assert "1 regression(s) exceed max-regressions 0" in msg
    assert "exceeds max-drift 10.0%" in msg
    assert "1 format change(s) exceed max-format-changes 0" in msg
    assert "1 check flip(s) from pass to fail exceed max-flips 0" in msg
    assert "regressed cases:" in msg and "case bad" in msg and "case ok" not in msg
    assert "reason: judge regressed; check pass_to_fail (regex): lost the number" in msg
    assert "format: text -> list" in msg
    assert "B: No idea No idea" in msg and msg.count("No idea") < 40   # truncated


def test_describe_case_without_checks_or_format_change():
    text = describe_case(_case("plain", "a\nb", "c"))
    assert text.splitlines() == ["case plain", "  drift:  0.5000 (moderate)",
                                 "  judge:  not judged", "  A: a b", "  B: c"]


# ── review fixes ───────────────────────────────────────────────────────────────

def test_collected_count_includes_generated_items(pytester):
    f = _write(pytester)
    pytester.makepyfile("def test_plain():\n    assert True\n")
    result = _run(pytester, "--promptdrift", f)
    result.stdout.fnmatch_lines(["collected 5 items*"])
    result.assert_outcomes(passed=3, failed=2)
    co = _run(pytester, "--co", "-q", "--promptdrift", f)
    co.stdout.fnmatch_lines(["5 tests collected*"])


def test_marker_deselection_count_is_not_negative(pytester):
    f = _write(pytester)
    pytester.makepyfile("def test_plain():\n    assert True\n")
    result = _run(pytester, "--promptdrift", f, "-m", "not promptdrift")
    result.stdout.fnmatch_lines(["collected 5 items / 4 deselected / 1 selected*"])
    result.assert_outcomes(passed=1, deselected=4)


def test_fixture_works_with_explicit_plugin_flag(pytester):
    """With -p promptdrift.pytest_plugin the shim's fixture wins and must build the report."""
    f = _write(pytester, [ROWS[0]])
    pytester.makepyfile(textwrap.dedent("""
        def test_fixture_has_report(promptdrift_report):
            assert promptdrift_report is not None
            assert [c.case_id for c in promptdrift_report.cases] == ["good"]
    """))
    result = _run(pytester, "-p", "promptdrift.pytest_plugin", "--promptdrift", f)
    result.assert_outcomes(passed=3)


@pytest.mark.parametrize("bad", ["10", "-0.5", "nan"])
def test_max_drift_option_must_be_a_fraction(pytester, bad):
    f = _write(pytester, [ROWS[0]])
    result = _run(pytester, "--promptdrift", f, "--promptdrift-max-drift", bad)
    assert result.ret != 0
    assert "fraction between 0 and 1" in result.stderr.str()
