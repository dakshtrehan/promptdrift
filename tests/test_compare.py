import importlib
import json

import pytest

from promptdrift import compare
from promptdrift.cli import main

from .conftest import FakeProvider, verdict_json

cmp = importlib.import_module("promptdrift.compare")

CFG_A = {"version": "v1", "provider": "openai", "model": "A", "system": "s",
         "prompt_template": "{input}", "temperature": 0.0, "max_tokens": 10}
CFG_B = dict(CFG_A, version="v2", model="B")
CASES = [{"id": "c1", "input": "x"}, {"id": "c2", "input": "y", "expected": "Y"}]


@pytest.fixture
def live(monkeypatch, no_keys):
    """Patch provider construction: models A/B answer from scripts, embeddings/judge by name."""
    state = {"A": FakeProvider(["apple pie", "same text"]),
             "B": FakeProvider(["car train", "same text"]),
             "built": []}

    def fake_get(name, **kw):
        state["built"].append((name, kw))
        if kw.get("model") in ("A", "B"):
            return state[kw["model"]]
        if kw.get("model", "").startswith("text-embedding") or name == "ollama":
            if state.get("embedder") is None:
                raise RuntimeError("no key")
            return state["embedder"]
        if state.get("judge") is None:
            raise RuntimeError("no judge")
        return state["judge"]
    monkeypatch.setattr(cmp, "get_provider", fake_get)
    monkeypatch.setattr("promptdrift.providers.get_provider", fake_get)
    return state


def test_compare_defaults_to_tfidf_without_embedding_calls(live, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # v0.1.1 would have used paid embeddings
    live["embedder"] = FakeProvider(vector=[1.0, 0.0])
    rep = compare(CFG_A, CFG_B, CASES, use_judge=False, verbose=False)
    assert rep.similarity == "tfidf" and rep.mode == "live"
    assert all(c.similarity_method == "tfidf" for c in rep.cases)
    assert not any(kw.get("model", "").startswith("text-embedding") for _, kw in live["built"])
    assert rep.cases[1].drift_level == "none"


def test_compare_embedding_opt_in(live):
    live["embedder"] = FakeProvider(vector=[1.0, 0.0])
    rep = compare(CFG_A, CFG_B, CASES, use_judge=False, verbose=False, similarity="embedding")
    assert rep.similarity == "embedding"
    assert rep.cases[0].similarity_method == "embedding" and rep.cases[0].drift_score == 0
    rep = compare(dict(CFG_A, embedding_model="text-embedding-3-large"), CFG_B, CASES,
                  use_judge=False, verbose=False)
    assert rep.similarity == "embedding"
    assert ("openai", {"model": "text-embedding-3-large"}) in live["built"]


def test_compare_embedding_unavailable_falls_back(live, capsys):
    rep = compare(CFG_A, CFG_B, CASES, use_judge=False, similarity="embedding")
    assert rep.similarity == "tfidf"
    assert "using TF-IDF" in capsys.readouterr().out
    with pytest.raises(ValueError, match="similarity must be"):
        compare(CFG_A, CFG_B, CASES, similarity="bm25", verbose=False)


def test_compare_ollama_embedder(live):
    live["embedder"] = FakeProvider(vector=[0.0, 1.0])
    cfg = dict(CFG_A, provider="ollama")
    rep = compare(cfg, dict(cfg, model="B"), CASES[:1], use_judge=False, verbose=False,
                  similarity="embedding")
    assert rep.similarity == "embedding"


def test_compare_judge_auto_anthropic(live, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    live["judge"] = FakeProvider([verdict_json("regressed", "lost"), verdict_json("improved")])
    rep = compare(CFG_A, CFG_B, CASES, checks={"regex": "e"})
    assert ("anthropic", {"model": "claude-haiku-4-5-20251001"}) in live["built"]
    c1, c2 = rep.cases
    assert c1.judge_verdict == "regressed" and c1.judge_consistent is True
    assert c2.judge_verdict is None  # no drift, judge not called
    assert len(live["judge"].calls) == 2
    out = capsys.readouterr().out
    assert "[2/2] c2" in out and "Judge : on" in out


def test_compare_no_judge_available(live, capsys):
    rep = compare(CFG_A, CFG_B, CASES)
    assert all(c.judge_verdict is None for c in rep.cases)
    assert "No judge available" in capsys.readouterr().out


def test_compare_from_files(live, tmp_path):
    import yaml
    a, b, c = tmp_path / "a.yaml", tmp_path / "b.json", tmp_path / "c.jsonl"
    a.write_text(yaml.safe_dump(CFG_A))
    b.write_text(json.dumps(CFG_B))
    c.write_text("\n".join(json.dumps(x) for x in CASES))
    rep = compare(a, b, c, use_judge=False, verbose=False, bootstrap=0)
    assert rep.total_cases == 2 and rep.version_b == "v2"
    assert rep.to_dict()["summary"]["ci"] is None


def test_aliases_kept_for_v011_callers():
    assert cmp._tfidf_similarity("a b", "a b") == pytest.approx(1.0)
    assert cmp._embed_pair(None, "a", "b") == 0.0
    assert cmp._judge(FakeProvider([verdict_json("improved")]), "q", "a", "b")[0] == "improved"
    assert cmp.render_prompt("Q: {input}", "hi") == "Q: hi"
    assert cmp.render_prompt('{"q": "{input}", "n": {x}}', "hi") == '{"q": "hi", "n": {x}}'


def test_resolve_embedder_failures(monkeypatch):
    def boom(name, **kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(cmp, "get_provider", boom)
    assert cmp._resolve_embedder({"provider": "ollama", "model": "m"}) is None
    assert cmp._resolve_embedder({"provider": "openai", "model": "m"}) is None


# ── CLI ──────────────────────────────────────────────────────────────────────
def _files(tmp_path):
    import yaml
    a, b, c = tmp_path / "v1.yaml", tmp_path / "v2.yaml", tmp_path / "golden.json"
    a.write_text(yaml.safe_dump(CFG_A))
    b.write_text(yaml.safe_dump(CFG_B))
    c.write_text(json.dumps(CASES))
    return str(a), str(b), str(c)


def test_cli_compare_end_to_end(live, tmp_path, capsys):
    a, b, c = _files(tmp_path)
    out = tmp_path / "r.json"
    main(["compare", a, b, "--cases", c, "--no-judge", "-q", "--show-outputs", "-o", str(out),
          "--samples", "2", "--bootstrap", "50", "--seed", "1"])
    data = json.loads(out.read_text())
    assert data["summary"]["stability"]["samples_per_side"] == 2
    assert data["summary"]["ci"]["n_bootstrap"] == 50
    text = capsys.readouterr().out
    assert "Report saved" in text and "A  ›" in text


def test_cli_compare_fail_on_regression(live, tmp_path):
    a, b, c = _files(tmp_path)
    cf = tmp_path / "checks.json"
    cf.write_text(json.dumps({"regex": "apple|same"}))
    with pytest.raises(SystemExit) as e:
        main(["compare", a, b, "--cases", c, "--no-judge", "-q", "--json", "--checks", str(cf),
              "--fail-on-regression"])
    assert e.value.code == 1


@pytest.mark.parametrize("setup,expect", [
    ("missing", "File not found"),
    ("badcfg", "Config error"),
    ("crash", "Error: kaboom"),
])
def test_cli_compare_errors(live, tmp_path, capsys, monkeypatch, setup, expect):
    a, b, c = _files(tmp_path)
    if setup == "missing":
        a = str(tmp_path / "nope.yaml")
    elif setup == "badcfg":
        (tmp_path / "v1.yaml").write_text("version: v1\n")
    else:
        def kaboom(*args, **kw):
            raise RuntimeError("kaboom")
        monkeypatch.setattr(cmp, "compare", kaboom)
    with pytest.raises(SystemExit) as e:
        main(["compare", a, b, "--cases", c, "--no-judge", "-q", "-v"])
    assert e.value.code == 1
    assert expect in capsys.readouterr().err


def test_cli_init_and_help(tmp_path, capsys):
    main(["init", "--dir", str(tmp_path / "p")])
    assert (tmp_path / "p" / "golden.json").exists()
    assert "Created" in capsys.readouterr().out
    main([])
    assert "usage" in capsys.readouterr().out.lower()


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "promptdrift" in capsys.readouterr().out
