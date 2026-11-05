"""Tests for threshold gates and the --max-* CLI flags. No network, no API keys."""
import json

import pytest

from promptdrift import evaluate_thresholds
from promptdrift.cli import main
from promptdrift.core import CaseResult, DiffReport
from promptdrift.offline import compare_outputs

RECORDS = [
    {"case_id": "same", "input": "q", "output_a": "Paris", "output_b": "Paris",
     "checks": {"regex": "Paris"}},
    {"case_id": "flip", "input": "15%", "output_a": "30", "output_b": "15% of 200 is 3.",
     "checks": {"regex": r"\b30\b"}},
    {"case_id": "fmt", "input": "list", "output_a": "a, b", "output_b": "- a\n- b\n- c",
     "tags": "lists"},
]


def report():
    return compare_outputs([dict(r) for r in RECORDS], version_a="v1", version_b="v2",
                           bootstrap=0)


def test_no_limits_means_no_failures():
    rep = report()
    assert evaluate_thresholds(rep) == []
    assert evaluate_thresholds(rep.to_dict()) == []


def test_each_gate_reports_a_sentence():
    rep = report()
    assert len(rep.regressions) == 1 and len(rep.format_changes) == 1
    out = evaluate_thresholds(rep, max_drift=0.01, max_regressions=0,
                              max_format_changes=0, max_flips=0, fail_on_regression=True)
    assert len(out) == 5
    pct = f"{round(rep.avg_drift * 100, 1)}%"
    assert out[0] == f"avg drift {pct} exceeds max-drift 1.0%"
    assert out[1] == "1 regression(s) found and fail-on-regression is set"
    assert out[2] == "1 regression(s) exceed max-regressions 0"
    assert out[3] == "1 format change(s) exceed max-format-changes 0"
    assert out[4] == "1 check flip(s) from pass to fail exceed max-flips 0"


def test_limits_are_inclusive():
    rep = report()
    assert evaluate_thresholds(rep, max_regressions=1, max_format_changes=1, max_flips=1,
                               max_drift=1.0) == []
    assert evaluate_thresholds(rep, max_drift=round(rep.avg_drift, 4)) == []


def test_dict_form_matches_report_form():
    rep = report()
    kw = dict(max_drift=0.05, max_regressions=0, max_format_changes=0, max_flips=0)
    assert evaluate_thresholds(rep, **kw) == evaluate_thresholds(rep.to_dict(), **kw)
    # a dict round-tripped through JSON works too (what the GitHub Action reads)
    assert evaluate_thresholds(json.loads(rep.to_json()), **kw) == evaluate_thresholds(rep, **kw)


def test_dict_without_checks_or_structure():
    data = {"summary": {"avg_drift": 0.2, "regressions": 0, "structural": None, "checks": None}}
    assert evaluate_thresholds(data, max_flips=0, max_format_changes=0) == []
    assert evaluate_thresholds(data, max_drift=0.1) == ["avg drift 20.0% exceeds max-drift 10.0%"]
    assert evaluate_thresholds({}, max_drift=0.0, max_regressions=0) == []


def test_flips_counts_only_pass_to_fail():
    c = CaseResult(case_id="x", input="i", output_a="a", output_b="b", similarity=1.0,
                   drift_score=0.0, drift_level="none", checks_passed_a=False,
                   checks_passed_b=True, check_flip="fail_to_pass")
    rep = DiffReport("a", "b", "m", "m", 1, [c], n_bootstrap=0)
    assert evaluate_thresholds(rep, max_flips=0) == []
    assert evaluate_thresholds(rep, max_regressions=0) == []


# ── CLI flags ─────────────────────────────────────────────────────────────────
@pytest.fixture
def outputs_file(tmp_path):
    p = tmp_path / "outputs.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in RECORDS))
    return str(p)


def test_cli_threshold_breach_exits_1_with_lines_on_stderr(outputs_file, capsys):
    with pytest.raises(SystemExit) as e:
        main(["diff", outputs_file, "--summary", "--bootstrap", "0", "--max-drift", "0.01",
              "--max-flips", "0"])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "exceeds max-drift 1.0%" in err
    assert "exceed max-flips 0" in err


def test_cli_thresholds_pass_exit_0(outputs_file, capsys):
    main(["diff", outputs_file, "--summary", "--bootstrap", "0", "--max-drift", "0.99",
          "--max-regressions", "5", "--max-format-changes", "5", "--max-flips", "5"])
    assert "exceed" not in capsys.readouterr().err


def test_cli_fail_on_regression_stays_silent(outputs_file, capsys):
    with pytest.raises(SystemExit) as e:
        main(["diff", outputs_file, "--summary", "--bootstrap", "0", "--fail-on-regression"])
    assert e.value.code == 1
    assert capsys.readouterr().err == ""


def test_compare_parser_has_threshold_flags():
    import argparse

    from promptdrift.cli import _add_output_args
    p = argparse.ArgumentParser()
    _add_output_args(p)
    ns = p.parse_args(["--max-drift", "0.2", "--max-regressions", "1",
                       "--max-format-changes", "2", "--max-flips", "3"])
    assert (ns.max_drift, ns.max_regressions, ns.max_format_changes, ns.max_flips) == (0.2, 1, 2, 3)
    assert p.parse_args([]).max_drift is None


# ── review fixes ──────────────────────────────────────────────────────────────
def _uneven_report():
    """Three cases whose mean drift does not round cleanly to 4 decimals."""
    cases = [CaseResult(str(i), "i", "a", "b", 1 - d, d, "minor")
             for i, d in enumerate((0.2, 0.2215, 0.244))]
    rep = DiffReport("a", "b", "m", "m", 3, cases, n_bootstrap=0)
    assert rep.avg_drift != round(rep.avg_drift, 4)
    return rep


@pytest.mark.parametrize("delta", [0.0, 0.00004, -0.00004, 0.0001, -0.0001])
def test_report_and_dict_form_gate_the_same_number(delta):
    rep = _uneven_report()
    limit = round(rep.avg_drift, 4) + delta
    assert evaluate_thresholds(rep, max_drift=limit) == \
        evaluate_thresholds(rep.to_dict(), max_drift=limit)
    assert evaluate_thresholds(rep, max_drift=limit) == \
        evaluate_thresholds(json.loads(rep.to_json()), max_drift=limit)


def test_failure_sentence_never_prints_equal_numbers():
    assert evaluate_thresholds({"summary": {"avg_drift": 0.1004}}, max_drift=0.10) == \
        ["avg drift 10.04% exceeds max-drift 10.00%"]
    assert evaluate_thresholds({"summary": {"avg_drift": 0.1001}}, max_drift=0.10009) == \
        ["avg drift 10.010% exceeds max-drift 10.009%"]
    # the usual case keeps the one decimal wording
    assert evaluate_thresholds({"summary": {"avg_drift": 0.123}}, max_drift=0.1) == \
        ["avg drift 12.3% exceeds max-drift 10.0%"]


@pytest.mark.parametrize("bad", [10, 15.0, -0.1, 1.0001, float("nan"), float("inf")])
def test_max_drift_outside_0_to_1_is_rejected(bad):
    with pytest.raises(ValueError, match="fraction between 0 and 1"):
        evaluate_thresholds({"summary": {"avg_drift": 0.5}}, max_drift=bad)


def test_max_drift_boundaries_are_accepted():
    data = {"summary": {"avg_drift": 0.5}}
    assert evaluate_thresholds(data, max_drift=1.0) == []
    assert evaluate_thresholds(data, max_drift=0.0) == ["avg drift 50.0% exceeds max-drift 0.0%"]
    assert evaluate_thresholds(data, max_drift=None) == []


@pytest.mark.parametrize("bad", ["10", "-0.1", "nan", "inf", "x"])
def test_cli_rejects_max_drift_in_percent(outputs_file, capsys, bad):
    with pytest.raises(SystemExit) as e:
        main(["diff", outputs_file, "--summary", "--max-drift", bad])
    assert e.value.code == 2
    assert "fraction between 0 and 1" in capsys.readouterr().err


def test_assert_no_regressions_rejects_max_drift_in_percent():
    from promptdrift import assert_no_regressions

    with pytest.raises(ValueError, match="fraction"):
        assert_no_regressions(report(), max_drift=10, max_regressions=99)
