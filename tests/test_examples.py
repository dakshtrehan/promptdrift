"""The examples directory must keep working: these tests run it.

Nothing here calls a model. API keys are blanked for every subprocess.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from promptdrift import compare_outputs, load_outputs

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
OFFLINE = EXAMPLES / "offline"

NO_KEYS = {**os.environ, "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "PYTHONIOENCODING": "utf-8"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(cmd, timeout=120, **kwargs):
    env = {**NO_KEYS, **kwargs.pop("env", {})}
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, **kwargs)


@pytest.fixture(scope="module")
def offline_report():
    with open(OFFLINE / "checks.yaml", encoding="utf-8") as f:
        checks = yaml.safe_load(f)
    return compare_outputs(OFFLINE / "outputs.jsonl", version_a="v1", version_b="v2",
                           checks=checks)


# ── offline scenario ─────────────────────────────────────────────────────────
def test_offline_dataset_shape():
    records = load_outputs(OFFLINE / "outputs.jsonl")
    assert len(records) == 16
    assert len({r["case_id"] for r in records}) == 16
    assert all(r.get("input") for r in records)
    assert all(isinstance(r.get("tags"), list) and r["tags"] for r in records)
    multi = [r for r in records if isinstance(r["output_a"], list)]
    assert [r["case_id"] for r in multi] == ["refund_subject_line"]
    assert len(multi[0]["output_a"]) == len(multi[0]["output_b"]) == 3
    kinds = set()
    for r in records:
        spec = r.get("checks")
        if isinstance(spec, dict):
            kinds |= set(spec)
        elif isinstance(spec, list):
            kinds |= {c["type"] for c in spec}
    assert kinds == {"regex", "json_keys", "exact"}


def test_offline_counts_match_readme(offline_report):
    r = offline_report
    assert r.total_cases == len(r.cases) == 16
    assert [c.case_id for c in r.regressions] == ["refund_window", "order_status_json"]
    assert [c.case_id for c in r.improvements] == ["cancel_plan_fee"]
    assert len(r.neutral) == 13
    assert sorted(c.case_id for c in r.format_changes) == [
        "invoice_json_fenced", "order_status_json", "reset_password_steps"]
    assert [c.case_id for c in r.json_broken] == ["order_status_json"]
    chk = r.checks_summary()
    assert chk == {"cases_with_checks": 16, "pass_rate_a": 0.9375, "pass_rate_b": 0.875,
                   "pass_to_fail": 2, "fail_to_pass": 1}
    by_id = {c.case_id: c for c in r.cases}
    assert by_id["refund_window"].check_flip == "pass_to_fail"
    assert by_id["refund_window"].drift_level == "regression"
    assert by_id["cancel_plan_fee"].check_flip == "fail_to_pass"
    assert by_id["reset_password_steps"].signature_a == "lines:1"
    assert by_id["reset_password_steps"].signature_b.startswith("list")or \
        by_id["reset_password_steps"].format_changed
    assert by_id["price_currency"].checks_passed_a and by_id["price_currency"].checks_passed_b
    identical = ["greeting", "ship_to_canada", "change_email", "address_change", "price_currency"]
    assert all(by_id[i].drift_level == "none" for i in identical)


def test_offline_multi_sample_noise_floor(offline_report):
    case = {c.case_id: c for c in offline_report.cases}["refund_subject_line"]
    assert case.n_samples == 3
    assert case.noise_floor is not None and case.excess_drift is not None
    assert case.excess_drift < 0  # versions differ less than each differs from itself
    # the default check catches the [Customer Name] placeholder in one of three B samples
    assert case.check_pass_rate_a == 1.0
    assert case.check_pass_rate_b == pytest.approx(0.6667, abs=1e-4)
    assert case.check_flip is None
    stab = offline_report.stability_summary()
    assert stab["cases"] == 1 and stab["cases_above_noise"] == 0


def test_offline_tags_reach_case_results(offline_report):
    """Workstream B adds CaseResult.tags; once present the offline records feed it."""
    case = offline_report.cases[0]
    if not hasattr(case, "tags"):
        pytest.skip("CaseResult.tags not in this build")
    assert case.tags == ["billing", "refund"]


def test_top_level_outputs_jsonl_still_works():
    records = load_outputs(EXAMPLES / "outputs.jsonl")
    assert [r["case_id"] for r in records] == ["capital", "percent", "extract", "steps", "reverse"]
    assert all("tags" in r for r in records)
    report = compare_outputs(records, version_a="v1", version_b="v2")
    assert len(report.regressions) == 1 and report.regressions[0].case_id == "percent"


def test_live_copies_match_originals():
    for name in ("v1.yaml", "v2.yaml", "golden.json"):
        assert (EXAMPLES / "live" / name).read_text() == (EXAMPLES / name).read_text()


# ── expected terminal output ─────────────────────────────────────────────────
KEY_LINES = ("Cases: 16", "Improved", "Structure", "Checks", "Stability", "DO NOT SHIP",
             "Regressions (2)")


def _key_lines(text):
    return [line.rstrip() for line in text.splitlines() if any(k in line for k in KEY_LINES)]


def test_expected_output_matches_a_fresh_run():
    expected = (OFFLINE / "expected_output.txt").read_text(encoding="utf-8")
    assert "\x1b[" not in expected  # colour codes stripped
    assert "saved" not in expected  # no machine specific paths
    proc = run([sys.executable, "-m", "promptdrift.cli", "diff", str(OFFLINE / "outputs.jsonl"),
                "--name-a", "v1", "--name-b", "v2", "--checks", str(OFFLINE / "checks.yaml")])
    assert proc.returncode == 0, proc.stderr
    fresh = ANSI.sub("", proc.stdout)
    assert _key_lines(fresh) == _key_lines(expected)
    assert len(_key_lines(expected)) >= 7
    assert "Regressed   2" in expected


# ── scripts ──────────────────────────────────────────────────────────────────
def test_python_api_script_runs():
    proc = run([sys.executable, str(EXAMPLES / "python_api.py")])
    assert proc.returncode == 0, proc.stderr
    assert "regressions: []" in proc.stdout
    assert "no regressions" in proc.stdout
    summary = json.loads(proc.stdout.split("\n}\n", 1)[0] + "\n}")
    assert summary["regressions"] == 0 and summary["stability"]["cases"] == 1


def test_mock_provider_script_runs_without_keys():
    proc = run([sys.executable, str(EXAMPLES / "live" / "mock_provider.py")])
    assert proc.returncode == 0, proc.stderr
    out = ANSI.sub("", proc.stdout)
    assert "mode: live" in out
    assert '"improvements": 5' in out
    assert '"inconsistent": 0' in out
    assert "scripted judge" in out


def test_mock_provider_never_builds_a_real_provider(monkeypatch):
    """The patched get_provider is the only provider path the example uses."""
    import importlib
    import importlib.util

    spec = importlib.util.spec_from_file_location("mock_provider", EXAMPLES / "live" / "mock_provider.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pc = importlib.import_module("promptdrift.compare")

    def boom(name, **kw):
        raise AssertionError("real provider constructed")

    monkeypatch.setattr(pc, "get_provider", boom)
    with pytest.raises(AssertionError):
        from promptdrift import compare
        compare({"version": "a", "provider": "openai", "model": "m", "prompt_template": "{input}",
                 "temperature": 0.0, "max_tokens": 1}, {"version": "b", "provider": "openai",
                 "model": "m", "prompt_template": "{input}", "temperature": 0.0, "max_tokens": 1},
                cases=[{"id": "x", "input": "y"}], use_judge=False, verbose=False)
    # the example patches get_provider itself, so it runs even with the real one broken
    assert mod.main() == 0


def test_ci_pytest_example(tmp_path):
    proc = run([sys.executable, "-m", "pytest", str(EXAMPLES / "ci" / "test_no_regressions.py"),
                "-q", "-p", "no:cacheprovider"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed, 2 xfailed" in proc.stdout


def test_ci_workflow_is_valid_yaml_and_not_registered():
    wf = yaml.safe_load((EXAMPLES / "ci" / "promptdrift.yml").read_text(encoding="utf-8"))
    assert "drift" in wf["jobs"]
    steps = wf["jobs"]["drift"]["steps"]
    run_step = next(s for s in steps if "promptdrift diff" in s.get("run", ""))
    assert "--fail-on-regression" in run_step["run"]
    assert run_step["env"] == {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}
    assert not (ROOT / ".github" / "workflows" / "promptdrift.yml").exists()


# ── every documented offline command runs ────────────────────────────────────
MARK = re.compile(r"^# test: offline(?:, exit (\d+))?\s*$")


def readme_blocks():
    """Fenced bash blocks in examples/README.md and examples/*/README.md marked for testing."""
    found = []
    for readme in [EXAMPLES / "README.md", *sorted(EXAMPLES.glob("*/README.md"))]:
        text = readme.read_text(encoding="utf-8")
        for block in re.findall(r"```bash\n(.*?)```", text, flags=re.S):
            lines = block.strip().splitlines()
            m = MARK.match(lines[0]) if lines else None
            if m:
                code = "\n".join(lines[1:])
                found.append(pytest.param(code, int(m.group(1) or 0),
                                          id=f"{readme.parent.name}:{code.splitlines()[0][:50]}"))
    return found


BLOCKS = readme_blocks()


def test_readme_has_marked_blocks():
    assert len(BLOCKS) >= 7


@pytest.mark.parametrize("code,expected_exit", BLOCKS)
def test_readme_offline_command(code, expected_exit, tmp_path):
    proc = run(["bash", "-c", code], timeout=240, env={"PROMPTDRIFT_OUT": str(tmp_path / "out")})
    assert proc.returncode == expected_exit, f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "Traceback" not in proc.stderr


def test_readme_offline_blocks_make_no_paid_calls():
    """Every tested block must be free: no --judge, no compare against a real config."""
    for param in BLOCKS:
        code = param.values[0]
        assert "--judge" not in code
        assert "promptdrift compare " not in code


# ── house rules ──────────────────────────────────────────────────────────────
def test_no_dashes_in_examples_and_benchmark():
    bad = []
    for d in (EXAMPLES, ROOT / "benchmark"):
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in {".md", ".py", ".yml", ".yaml", ".sh", ".jsonl", ".json", ".txt"}:
                text = p.read_text(encoding="utf-8")
                if "\u2014" in text or "\u2013" in text:  # em dash, en dash
                    bad.append(str(p.relative_to(ROOT)))
    assert not bad, bad
