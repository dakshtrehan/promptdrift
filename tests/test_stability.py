
import importlib

import pytest

from promptdrift import compare, compare_outputs
from promptdrift.reporter import print_report
from promptdrift.scoring import pairwise_drift, score_case

from .conftest import FakeProvider

cmp = importlib.import_module("promptdrift.compare")


def test_pairwise_drift():
    assert pairwise_drift(["a"]) is None
    assert pairwise_drift(["x y", "x y"]) == pytest.approx(0.0)
    assert pairwise_drift(["apple", "car", "apple"]) == pytest.approx(2 / 3)


def test_score_case_samples_noise_floor():
    # A is stable, B is noisy; cross drift should exceed the floor
    r = score_case({"id": "c"}, ["red cat", "red cat"], ["red cat", "blue dog"])
    assert r.n_samples == 2 and r.samples_b == ["red cat", "blue dog"]
    assert r.within_drift_a == 0.0 and r.within_drift_b == pytest.approx(1.0)
    assert r.noise_floor == pytest.approx(0.5)
    assert r.drift_score == pytest.approx(0.5)
    assert r.excess_drift == pytest.approx(0.0)
    assert r.output_a == "red cat"  # first sample kept for back-compat fields


def test_score_case_single_vs_multi_and_empty():
    r = score_case({"id": "c"}, "same", ["same", "same", "same"])
    assert r.within_drift_a is None and r.within_drift_b == 0.0
    assert r.noise_floor == 0.0 and r.excess_drift == 0.0
    with pytest.raises(ValueError, match="at least one"):
        score_case({"id": "c"}, [], "x")


def test_score_case_checks_majority_across_samples():
    r = score_case({"id": "c", "checks": {"regex": r"\d"}}, ["1", "2", "x"], ["x", "y", "3"])
    assert (r.check_pass_rate_a, r.check_pass_rate_b) == (0.6667, 0.3333)
    assert r.check_flip == "pass_to_fail"


def test_embedding_cached_per_text():
    emb = FakeProvider(vector=lambda t: [1.0, float(len(t))])
    r = score_case({"id": "c"}, ["aa", "aa", "b"], ["aa", "b"], embedder=emb)
    assert r.similarity_method == "embedding"
    assert len(emb.calls) == 0  # complete() never called
    # FakeProvider records embed calls only through its vector callable; check via a counter
    seen = []
    emb2 = FakeProvider(vector=lambda t: seen.append(t) or [1.0, 2.0])
    score_case({"id": "c"}, ["aa", "aa", "b"], ["aa", "b"], embedder=emb2)
    assert sorted(seen) == ["aa", "b"]


def test_mixed_method_when_embedder_flaky():
    state = {"n": 0}

    def vec(t):
        state["n"] += 1
        if t == "boom":
            raise RuntimeError("fail")
        return [1.0, 0.0]
    r = score_case({"id": "c"}, ["ok", "boom"], ["ok"], embedder=FakeProvider(vector=vec))
    assert r.similarity_method == "mixed"


def test_offline_samples_summary_and_report(capsys):
    rep = compare_outputs([
        {"case_id": "a", "output_a": ["x y", "x y"], "output_b": ["p q", None]},
        {"case_id": "b", "output_a": "same", "output_b": "same"},
    ])
    st = rep.stability_summary()
    assert st["cases"] == 1 and st["samples_per_side"] == 2
    assert st["cases_above_noise"] == 1
    assert rep.to_dict()["summary"]["stability"]["cases"] == 1
    print_report(rep)
    out = capsys.readouterr().out
    assert "Stability" in out and "excess=" in out
    assert compare_outputs([{"case_id": "x", "output_a": "a", "output_b": "a"}]
                           ).stability_summary() is None


def test_offline_rejects_bad_sample_list():
    with pytest.raises(ValueError, match="list of strings"):
        compare_outputs([{"case_id": "a", "output_a": [], "output_b": "x"}])
    with pytest.raises(ValueError, match="list of strings"):
        compare_outputs([{"case_id": "a", "output_a": [1], "output_b": "x"}])


def _patch_live(monkeypatch, a_replies, b_replies):
    provs = {"A": FakeProvider(a_replies), "B": FakeProvider(b_replies)}
    monkeypatch.setattr(cmp, "get_provider", lambda name, **kw: provs[kw["model"]])
    return provs


CFG_A = {"version": "v1", "provider": "x", "model": "A", "system": "s",
         "prompt_template": "Q: {input}", "temperature": 0.0, "max_tokens": 10}
CFG_B = dict(CFG_A, version="v2", model="B", prompt_template='{"q": "{input}"}')


def test_live_compare_with_samples(monkeypatch):
    provs = _patch_live(monkeypatch, ["one", "one"], ["one", "two"])
    rep = compare(CFG_A, CFG_B, [{"id": "c", "input": "hi"}], use_judge=False,
                  verbose=False, samples=2)
    c = rep.cases[0]
    assert c.n_samples == 2 and c.samples_b == ["one", "two"]
    assert c.tokens_a == 16 and c.latency_a == 0.1
    assert len(provs["A"].calls) == 2
    assert provs["A"].calls[0]["prompt"] == "Q: hi"
    assert provs["B"].calls[0]["prompt"] == '{"q": "hi"}'  # literal braces handled
    with pytest.raises(ValueError, match="samples"):
        compare(CFG_A, CFG_B, [{"id": "c", "input": "hi"}], samples=0, verbose=False)
