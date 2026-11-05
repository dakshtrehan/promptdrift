import json

import pytest

from promptdrift import compare_outputs
from promptdrift.checks import check_flip, normalize_checks, run_check, run_checks
from promptdrift.cli import main
from promptdrift.reporter import print_report


def test_normalize_shorthand_and_expected():
    checks = normalize_checks({"regex": r"\d+", "json_keys": "name", "exact": True},
                              expected="96")
    assert [c["type"] for c in checks] == ["regex", "json_keys", "exact"]
    assert checks[1]["keys"] == ["name"] and checks[2]["value"] == "96"
    assert normalize_checks({"exact": 5})[0]["value"] == "5"
    assert normalize_checks({"type": "regex", "pattern": "x"})[0]["pattern"] == "x"
    assert normalize_checks(None) == []


@pytest.mark.parametrize("spec,msg", [
    ({"nope": 1}, "Unknown check"),
    ("regex", "must be a list"),
    ([{"type": "bogus"}], "'type' must be"),
    ([{"type": "regex"}], "string 'pattern'"),
    ([{"type": "regex", "pattern": "x", "flags": "q"}], "Unknown regex flag"),
    ([{"type": "regex", "pattern": "("}], "Invalid regex"),
    ([{"type": "json_keys", "keys": []}], "non-empty"),
    ([{"type": "exact"}], "no 'expected'"),
])
def test_normalize_errors(spec, msg):
    with pytest.raises(ValueError, match=msg):
        normalize_checks(spec)


def test_run_regex_flags():
    chk = normalize_checks([{"type": "regex", "pattern": "^paris$", "flags": "im"}])[0]
    assert run_check("Answer:\nParis", chk)["passed"]


@pytest.mark.parametrize("text,passed,detail", [
    ('{"name": 1, "age": 2}', True, "all keys present"),
    ('```json\n{"name": 1}\n```', False, "missing ['age']"),
    ("[1]", False, "not an object"),
    ("nope", False, "not valid JSON"),
])
def test_run_json_keys(text, passed, detail):
    r = run_check(text, {"type": "json_keys", "keys": ["name", "age"]})
    assert r["passed"] is passed and detail in r["detail"]


def test_run_exact_options():
    assert run_check(" 96 \n", {"type": "exact", "value": "96"})["passed"]
    assert not run_check(" 96 ", {"type": "exact", "value": "96", "strip": False})["passed"]
    assert run_check("PARIS", {"type": "exact", "value": "paris", "ignore_case": True})["passed"]
    assert not run_check("PARIS", {"type": "exact", "value": "paris"})["passed"]


def test_run_checks_all_must_pass():
    checks = normalize_checks({"regex": "a", "exact": "b"})
    assert run_checks("a", checks)["passed"] is False


@pytest.mark.parametrize("a,b,flip", [
    (True, False, "pass_to_fail"), (False, True, "fail_to_pass"),
    (True, True, None), (None, True, None),
])
def test_check_flip(a, b, flip):
    assert check_flip(a, b) == flip


def test_flip_counts_as_regression(capsys):
    rep = compare_outputs([
        {"case_id": "broke", "output_a": '{"name": "x"}', "output_b": "name is x",
         "checks": {"json_keys": ["name"]}},
        {"case_id": "fixed", "output_a": "about 96", "output_b": "96",
         "expected": "96", "checks": {"exact": True}},
        {"case_id": "nochecks", "output_a": "a", "output_b": "a"},
    ])
    broke, fixed, plain = rep.cases
    assert broke.check_flip == "pass_to_fail" and broke.drift_level == "regression"
    assert fixed.check_flip == "fail_to_pass"
    assert plain.checks_passed_a is None
    assert rep.regressions == [broke] and rep.improvements == [fixed] and rep.neutral == [plain]
    cs = rep.checks_summary()
    assert cs == {"cases_with_checks": 2, "pass_rate_a": 0.5, "pass_rate_b": 0.5,
                  "pass_to_fail": 1, "fail_to_pass": 1}
    print_report(rep)
    out = capsys.readouterr().out
    assert "pass to fail (json_keys)" in out and "fail to pass (exact)" in out
    assert "Checks" in out


def test_default_checks_and_no_checks_summary():
    rep = compare_outputs([{"case_id": "a", "output_a": "x1", "output_b": "y"}],
                          checks=[{"type": "regex", "pattern": r"\d"}])
    assert rep.cases[0].check_flip == "pass_to_fail"
    assert compare_outputs([{"case_id": "a", "output_a": "x", "output_b": "x"}]
                           ).checks_summary() is None


def test_cli_checks_file(tmp_path):
    out = tmp_path / "o.jsonl"
    out.write_text(json.dumps({"case_id": "a", "output_a": "42", "output_b": "forty two"}))
    for name, body in [("c.json", json.dumps({"regex": r"\d"})), ("c.yaml", "regex: '\\d'\n")]:
        cf = tmp_path / name
        cf.write_text(body)
        with pytest.raises(SystemExit) as e:
            main(["diff", str(out), "--checks", str(cf), "--summary", "--fail-on-regression"])
        assert e.value.code == 1
