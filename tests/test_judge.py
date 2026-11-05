import importlib

import pytest

from promptdrift import compare_outputs
from promptdrift.judge import (
    DEFAULT_JUDGE_MODELS,
    build_judge_prompt,
    default_judge_provider,
    judge_both_orders,
    judge_pair,
    parse_verdict,
    resolve_judge,
)
from promptdrift.reporter import print_report

from .conftest import FakeProvider, verdict_json

judge_mod = importlib.import_module("promptdrift.judge")


def test_build_prompt_expected_line():
    assert "Expected answer: 96" in build_judge_prompt("q", "a", "b", expected="96")
    assert "Expected answer" not in build_judge_prompt("q", "a", "b")


@pytest.mark.parametrize("text,out", [
    ('{"verdict": "Regressed", "reasoning": "x"}', ("regressed", "x")),
    ('Sure: {"verdict": "neutral"} done', ("neutral", "")),
    ('{"verdict": "better"}', None),
    ("[1, 2]", None),
    ('["verdict"]', None),
    ("{not json}", None),
    ("", None),
    (None, None),
])
def test_parse_verdict(text, out):
    assert parse_verdict(text) == out


def test_judge_pair_and_failures():
    assert judge_pair(FakeProvider([verdict_json("improved", "y")]), "q", "a", "b") == ("improved", "y")
    assert judge_pair(FakeProvider(fail=True), "q", "a", "b") == ("neutral", "")
    assert judge_pair(FakeProvider(["garbage"]), "q", "a", "b") == ("neutral", "")


def test_both_orders_consistent_and_prompt_order():
    p = FakeProvider([verdict_json("regressed", "lost info"), verdict_json("improved")])
    r = judge_both_orders(p, "q", "AAA", "BBB")
    assert r == {"verdict": "regressed", "reasoning": "lost info", "verdict_ab": "regressed",
                 "verdict_ba": "regressed", "consistent": True}
    first, second = p.calls[0]["prompt"], p.calls[1]["prompt"]
    assert first.index("AAA") < first.index("BBB")
    assert second.index("BBB") < second.index("AAA")
    assert p.calls[0]["max_tokens"] == 150


def test_both_orders_inconsistent_and_error():
    r = judge_both_orders(FakeProvider([verdict_json("improved")]), "q", "a", "b")
    assert r["verdict"] == "inconsistent" and r["consistent"] is False
    assert r["verdict_ba"] == "regressed" and "A-first said improved" in r["reasoning"]
    r = judge_both_orders(FakeProvider([verdict_json("neutral"), "junk"]), "q", "a", "b")
    assert r["verdict"] == "error" and r["consistent"] is None and r["verdict_ab"] == "neutral"


def test_default_judge_provider(monkeypatch, no_keys):
    assert default_judge_provider() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert default_judge_provider() == "anthropic"
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    assert default_judge_provider() == "openai"


def test_resolve_judge(monkeypatch, no_keys, capsys):
    fake = FakeProvider()
    assert resolve_judge(fake) is fake
    assert resolve_judge(None, verbose=True) is None
    assert "No judge available" in capsys.readouterr().out

    built = {}

    def fake_get(name, **kw):
        built.clear()
        built.update(name=name, **kw)
        return fake
    monkeypatch.setattr("promptdrift.providers.get_provider", fake_get)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert resolve_judge() is fake
    assert built == {"name": "anthropic", "model": DEFAULT_JUDGE_MODELS["anthropic"]}
    resolve_judge("openai", "gpt-x")
    assert built == {"name": "openai", "model": "gpt-x"}
    resolve_judge("custom")
    assert built == {"name": "custom"}

    def boom(name, **kw):
        raise ImportError("pip install anthropic")
    monkeypatch.setattr("promptdrift.providers.get_provider", boom)
    assert resolve_judge("anthropic", verbose=True) is None
    assert "unavailable" in capsys.readouterr().out


def test_report_judge_summary_and_single_order(capsys):
    rows = [{"case_id": "a", "output_a": "apple", "output_b": "car"},
            {"case_id": "b", "output_a": "pear", "output_b": "boat"}]
    judge = FakeProvider([verdict_json("improved")])  # always "B better": position bias
    rep = compare_outputs(rows, use_judge=True, judge_provider=judge)
    assert [c.judge_verdict for c in rep.cases] == ["inconsistent", "inconsistent"]
    assert rep.inconsistent == rep.cases and not rep.regressions and not rep.improvements
    js = rep.judge_summary()
    assert js == {"judged": 2, "both_orders": 2, "inconsistent": 2, "inconsistency_rate": 1.0,
                  "errors": 0}
    print_report(rep)
    out = capsys.readouterr().out
    assert "order-inconsistent 2" in out and "? inconsistent" in out

    single = compare_outputs(rows, use_judge=True, judge_provider=FakeProvider(
        [verdict_json("improved")]), judge_both_orders=False)
    assert single.improvements == single.cases
    assert single.judge_summary()["inconsistency_rate"] is None
    assert single.cases[0].judge_consistent is None


def test_report_judge_errors_shown(capsys):
    rep = compare_outputs([{"case_id": "a", "output_a": "apple", "output_b": "car"}],
                          use_judge=True, judge_provider=FakeProvider(fail=True))
    assert rep.judge_summary()["errors"] == 1
    print_report(rep)
    assert "errors 1" in capsys.readouterr().out
