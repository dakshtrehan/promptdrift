"""Tests for promptdrift. No API keys needed."""
import json

import pytest

from promptdrift.compare import _tfidf_similarity, load_cases, load_config
from promptdrift.core import CaseResult, DiffReport, classify_drift, cosine_similarity
from promptdrift.reporter import print_report


# ── Cosine similarity ─────────────────────────────────────────────────────────
def test_cosine_identical():
    v = [1.0, 0.5, 0.3]
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

def test_cosine_orthogonal():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-6)

def test_cosine_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


# ── Drift classification ──────────────────────────────────────────────────────
def test_classify_none():    assert classify_drift(0.98) == "none"
def test_classify_minor():   assert classify_drift(0.88) == "minor"
def test_classify_moderate():assert classify_drift(0.65) == "moderate"
def test_classify_major():   assert classify_drift(0.40) == "major"


# ── TF-IDF fallback ───────────────────────────────────────────────────────────
def test_tfidf_identical():
    s = "the quick brown fox"
    assert _tfidf_similarity(s, s) == pytest.approx(1.0, abs=1e-6)

def test_tfidf_disjoint():
    assert _tfidf_similarity("apple banana", "car train") == pytest.approx(0.0, abs=1e-6)

def test_tfidf_partial_overlap():
    sim = _tfidf_similarity("the cat sat on the mat", "the dog sat on the mat")
    assert 0.5 < sim < 1.0


# ── Report aggregations ───────────────────────────────────────────────────────
def _report(cases_data):
    r = DiffReport(version_a="v1", version_b="v2", model_a="m", model_b="m", total_cases=len(cases_data))
    for d in cases_data:
        r.cases.append(CaseResult(**d))
    return r

def test_regression_rate():
    r = _report([
        dict(case_id="a", input="x", output_a="foo", output_b="bar", similarity=0.3, drift_score=0.7, drift_level="regression", judge_verdict="regressed"),
        dict(case_id="b", input="y", output_a="c", output_b="c", similarity=0.98, drift_score=0.02, drift_level="none", judge_verdict="neutral"),
    ])
    assert r.regression_rate == pytest.approx(0.5)
    assert len(r.regressions) == 1

def test_avg_drift():
    r = _report([
        dict(case_id="a", input="x", output_a="a", output_b="b", similarity=0.8, drift_score=0.2, drift_level="minor"),
        dict(case_id="b", input="y", output_a="c", output_b="d", similarity=0.6, drift_score=0.4, drift_level="moderate"),
    ])
    assert r.avg_drift == pytest.approx(0.3, abs=1e-6)

def test_to_json():
    r = _report([dict(case_id="a", input="x", output_a="a", output_b="b", similarity=0.9, drift_score=0.1, drift_level="minor")])
    data = json.loads(r.to_json())
    assert data["version_a"] == "v1"
    assert data["summary"]["avg_drift"] == pytest.approx(0.1)


# ── Config loader ─────────────────────────────────────────────────────────────
def test_load_config_valid(tmp_path):
    f = tmp_path / "v1.yaml"
    f.write_text(
        "version: v1\n"
        "provider: openai\n"
        "model: gpt-4o-mini\n"
        "system: You are helpful.\n"
        "prompt_template: '{input}'\n"
        "temperature: 0.0\n"
    )
    cfg = load_config(f)
    assert cfg["version"] == "v1"
    assert cfg["max_tokens"] == 1024  # default applied

def test_load_config_missing(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("version: v1\nprovider: openai\n")
    with pytest.raises(ValueError, match="Missing fields"):
        load_config(f)

def test_load_config_json(tmp_path):
    f = tmp_path / "v1.json"
    f.write_text(json.dumps({
        "version": "v1", "provider": "openai", "model": "gpt-4o-mini",
        "prompt_template": "{input}",
    }))
    cfg = load_config(f)
    assert cfg["temperature"] == 0.0  # default


# ── Cases loader ──────────────────────────────────────────────────────────────
def test_load_cases_auto_id(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps([{"input": "hello"}, {"input": "world"}]))
    cases = load_cases(f)
    assert cases[0]["id"] == "case_1"
    assert cases[1]["id"] == "case_2"

def test_load_cases_missing_input(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text(json.dumps([{"id": "x"}]))
    with pytest.raises(ValueError, match="missing 'input'"):
        load_cases(f)

def test_load_cases_jsonl(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text('{"id": "a", "input": "hi"}\n{"id": "b", "input": "bye"}\n')
    cases = load_cases(f)
    assert len(cases) == 2
    assert cases[0]["id"] == "a"

def test_load_cases_yaml(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("- id: a\n  input: hi\n- id: b\n  input: bye\n")
    cases = load_cases(f)
    assert cases[1]["input"] == "bye"


# ── Reporter smoke test ───────────────────────────────────────────────────────
def test_print_report_smoke(capsys):
    r = _report([dict(case_id="s", input="test", output_a="a", output_b="b",
                      similarity=0.85, drift_score=0.15, drift_level="minor",
                      judge_verdict="neutral", judge_reasoning="ok", latency_a=0.3, latency_b=0.4)])
    print_report(r)
    out = capsys.readouterr().out
    assert "promptdrift" in out and "v1" in out


def test_verdict_lines(capsys):
    from promptdrift import compare_outputs
    print_report(compare_outputs([{"case_id": "a", "output_a": "x y", "output_b": "x y"}]))
    assert "SAFE TO SHIP" in capsys.readouterr().out
    print_report(compare_outputs([{"case_id": "a", "output_a": "- x y", "output_b": "x y"}]))
    out = capsys.readouterr().out
    assert "REVIEW" in out and "format change" in out
