"""Tests added while hardening v0.3.0: config-dict defaults, drift clamping,
public API surface and the strict typing helpers."""
from __future__ import annotations

import math
import sys
from unittest import mock

import pytest

import promptdrift
from promptdrift import compare, compare_outputs
from promptdrift.providers import LLMResponse, SupportsComplete, SupportsEmbed
from promptdrift.scoring import pairwise_drift, score_case
from promptdrift.structure import _JSON_TYPES


class _Scripted:
    """Generation provider that echoes the prompt, for live-mode tests without a key."""

    def __init__(self, *args, **kwargs):
        self.calls: list[tuple[str, str, dict]] = []

    def complete(self, system, prompt, **kwargs):
        self.calls.append((system, prompt, kwargs))
        return LLMResponse(text=f"echo: {prompt}", latency=0.01)

    def embed(self, text):  # pragma: no cover - never reached with tfidf similarity
        raise RuntimeError("no embeddings here")


def test_compare_accepts_config_dicts_without_optional_fields():
    """A dict config without system/temperature/max_tokens used to raise KeyError."""
    made: list[_Scripted] = []

    def fake_get_provider(name, **kwargs):
        p = _Scripted()
        made.append(p)
        return p

    minimal_a = {"version": "v1", "provider": "openai", "model": "m", "prompt_template": "{input}"}
    minimal_b = {"version": "v2", "provider": "openai", "model": "m",
                 "prompt_template": "Answer briefly: {input}"}
    # ``promptdrift.compare`` resolves to the re-exported function, so patch the module object
    with mock.patch.object(sys.modules["promptdrift.compare"], "get_provider", fake_get_provider):
        report = compare(minimal_a, minimal_b, [{"id": "c1", "input": "hi"}],
                         use_judge=False, verbose=False, bootstrap=0)
    assert report.total_cases == 1
    system, prompt, kwargs = made[0].calls[0]
    assert system == ""
    assert kwargs == {"temperature": 0.0, "max_tokens": 1024}
    assert made[1].calls[0][1] == "Answer briefly: hi"


def test_identical_outputs_never_report_negative_zero_drift():
    report = compare_outputs([{"case_id": "same", "output_a": "Paris is the capital.",
                               "output_b": "Paris is the capital."}], bootstrap=0)
    c = report.cases[0]
    assert c.drift_score == 0.0 and not math.copysign(1.0, c.drift_score) < 0
    assert "-0.0" not in report.to_json()
    assert c.similarity == 1.0


def test_pairwise_drift_of_identical_samples_is_positive_zero():
    d = pairwise_drift(["same text", "same text", "same text"])
    assert d is not None and round(d, 4) == 0.0 and math.copysign(1.0, d) > 0
    assert pairwise_drift(["only one"]) is None


def test_multi_sample_identical_case_has_zero_noise_floor():
    c = score_case({"id": "x"}, ["a b c", "a b c"], ["a b c", "a b c"])
    assert c.noise_floor == 0.0 and c.excess_drift == 0.0
    assert "-0.0" not in repr(c)


def test_protocols_are_structural():
    class Judge:
        def complete(self, system, prompt, **kwargs):
            return LLMResponse(text='{"verdict": "neutral", "reasoning": ""}')

    class Embedder:
        def embed(self, text):  # orthogonal vectors so the drift is large and the judge runs
            return mock.Mock(vector=[1.0, 0.0] if "x" in text else [0.0, 1.0])

    # The protocols are not runtime_checkable (duck typing only); they are usable
    # wherever a judge or embedder is expected without inheriting from anything.
    assert SupportsComplete is not None and SupportsEmbed is not None
    report = compare_outputs([{"case_id": "c", "output_a": "x y", "output_b": "y z"}],
                             use_judge=True, judge_provider=Judge(), embedder=Embedder(),
                             bootstrap=0)
    assert report.cases[0].judge_verdict == "neutral"
    assert report.cases[0].similarity_method == "embedding"


def test_json_types_table_covers_containers_only():
    assert _JSON_TYPES == {dict: "object", list: "array"}


def test_public_api_surface_from_v020_is_intact():
    """Every name documented for v0.2.0 (and the v0.1.1 private aliases) still imports."""
    names = ["compare", "load_config", "load_cases", "compare_outputs", "load_outputs",
             "DiffReport", "CaseResult", "print_report", "save_report", "print_json",
             "__version__"]
    for n in names:
        assert n in promptdrift.__all__ and hasattr(promptdrift, n)
    from promptdrift.compare import _embed_pair, _judge, _tfidf_similarity  # noqa: F401
    from promptdrift.similarity import text_similarity, tfidf_similarity
    assert _tfidf_similarity("a b", "a b") == tfidf_similarity("a b", "a b")
    assert tfidf_similarity("a b", "a b") == pytest.approx(1.0)
    assert text_similarity("a", "b")[1] == "tfidf"
    # new fields were appended with defaults, so positional construction from v0.2.0 works
    c = promptdrift.CaseResult("id", "in", "a", "b", 1.0, 0.0, "none")
    assert c.tags is None and c.judge_verdict is None
