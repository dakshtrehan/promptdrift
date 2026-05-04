"""
Main compare engine. Runs prompt versions, embeds outputs, scores drift.
"""
from __future__ import annotations
import json
import math
import re
from collections import Counter
from pathlib import Path

import yaml

from .core import CaseResult, DiffReport, cosine_similarity, classify_drift
from .providers import BaseProvider, get_provider


# ── Config / case loaders ─────────────────────────────────────────────────────
def load_config(path: str | Path) -> dict:
    """
    Load a prompt config (YAML or JSON).

    Required fields: version, model, provider, prompt_template
    Optional: system, temperature, max_tokens, embedding_model

    Example:
        version: v2
        provider: openai
        model: gpt-4o-mini
        system: "You are a helpful assistant."
        prompt_template: "{input}"
        temperature: 0.0
        max_tokens: 1024
    """
    p = Path(path)
    with open(p) as f:
        cfg = yaml.safe_load(f) if p.suffix in (".yaml", ".yml") else json.load(f)
    required = ["version", "provider", "model", "prompt_template"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(f"{p.name}: Missing fields: {missing}")
    cfg.setdefault("system", "")
    cfg.setdefault("temperature", 0.0)
    cfg.setdefault("max_tokens", 1024)
    return cfg


def load_cases(path: str | Path) -> list[dict]:
    """
    Load test cases from JSON, JSONL, or YAML.

    Each case needs at least 'input'. 'id' is auto-assigned if missing.
    'expected' is optional metadata, passed to the judge for context.

    Example:
        [
          {"id": "greet", "input": "Say hello", "expected": "Hello!"},
          {"input": "What is 2+2?"}
        ]
    """
    p = Path(path)
    suffix = p.suffix.lower()
    with open(p) as f:
        if suffix in (".yaml", ".yml"):
            cases = yaml.safe_load(f)
        elif suffix == ".jsonl":
            cases = [json.loads(line) for line in f if line.strip()]
        else:
            cases = json.load(f)
    if not isinstance(cases, list):
        raise ValueError(f"{path}: Must be a list/array.")
    for i, c in enumerate(cases):
        if "input" not in c:
            raise ValueError(f"Case {i}: missing 'input' field.")
        if "id" not in c:
            c["id"] = f"case_{i+1}"
    return cases


# ── TF-IDF fallback (offline, zero-cost similarity) ───────────────────────────
def _tfidf_similarity(text_a: str, text_b: str) -> float:
    """Tiny TF-IDF cosine similarity. Used when no embedding provider is available."""
    tok_a = re.findall(r"\b\w+\b", text_a.lower())
    tok_b = re.findall(r"\b\w+\b", text_b.lower())
    vocab = sorted(set(tok_a) | set(tok_b))
    if not vocab:
        return 1.0
    idx = {w: i for i, w in enumerate(vocab)}
    vec_a = [0.0] * len(vocab)
    vec_b = [0.0] * len(vocab)
    ca, cb = Counter(tok_a), Counter(tok_b)
    ta = sum(ca.values()) or 1
    tb = sum(cb.values()) or 1
    for w, i in idx.items():
        vec_a[i] = ca.get(w, 0) / ta
        vec_b[i] = cb.get(w, 0) / tb
    return cosine_similarity(vec_a, vec_b)


def _embed_pair(emb_prov: BaseProvider | None, text_a: str, text_b: str) -> float:
    """Return cosine similarity between two texts. Falls back to TF-IDF on failure."""
    if emb_prov is not None:
        try:
            ea = emb_prov.embed(text_a).vector
            eb = emb_prov.embed(text_b).vector
            return cosine_similarity(ea, eb)
        except Exception:
            pass
    return _tfidf_similarity(text_a, text_b)


# ── LLM judge ─────────────────────────────────────────────────────────────────
def _judge(provider: BaseProvider, inp: str, out_a: str, out_b: str,
           expected: str | None = None) -> tuple[str, str]:
    """
    Ask an LLM to compare A vs B and return (verdict, reasoning).
    verdict is one of: improved | regressed | neutral
    """
    exp_line = f"\nExpected answer: {expected}" if expected else ""
    prompt = f"""Compare two AI outputs for the same input.{exp_line}

Input: {inp}

Output A (baseline): {out_a}

Output B (new version): {out_b}

Is Output B improved, regressed, or neutral compared to A?
- improved: clearly more accurate, helpful, or complete
- regressed: less accurate, introduces errors, or loses important information
- neutral: equivalent quality, just phrased differently

Respond ONLY with this JSON (no markdown, no extra text):
{{"verdict": "improved|regressed|neutral", "reasoning": "one sentence"}}"""

    try:
        r = provider.complete(
            "You evaluate AI output quality. Return only valid JSON.",
            prompt, max_tokens=150,
        )
        text = r.text.strip()
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            return data.get("verdict", "neutral"), data.get("reasoning", "")
    except Exception:
        pass
    return "neutral", ""


# ── Embedding provider resolution ─────────────────────────────────────────────
def _resolve_embedder(cfg: dict) -> BaseProvider | None:
    """
    Try to build an embedding provider for the given config.
    Returns None if no embedding provider is reachable; caller should fall back to TF-IDF.
    """
    # Explicit override
    emb_model = cfg.get("embedding_model")
    # Ollama supports embeddings natively
    if cfg["provider"] == "ollama":
        try:
            return get_provider("ollama", model=emb_model or cfg["model"])
        except Exception:
            return None
    # OpenAI is the most common embedding source
    try:
        return get_provider("openai", model=emb_model or "text-embedding-3-small")
    except Exception:
        return None


# ── Main compare function ─────────────────────────────────────────────────────
def compare(
    config_a: str | Path | dict,
    config_b: str | Path | dict,
    cases: str | Path | list[dict],
    *,
    use_judge: bool = True,
    judge_provider: str = "openai",
    judge_model: str = "gpt-4o-mini",
    verbose: bool = True,
) -> DiffReport:
    """
    Compare two prompt versions across a set of test cases.

    Returns a DiffReport with per-case semantic drift, judge verdicts,
    latency deltas, and an overall regression summary.
    """
    cfg_a = load_config(config_a) if not isinstance(config_a, dict) else config_a
    cfg_b = load_config(config_b) if not isinstance(config_b, dict) else config_b
    test_cases = load_cases(cases) if not isinstance(cases, list) else cases

    if verbose:
        print(f"\n⚡ promptdrift")
        print(f"   A : {cfg_a['version']} ({cfg_a['provider']} / {cfg_a['model']})")
        print(f"   B : {cfg_b['version']} ({cfg_b['provider']} / {cfg_b['model']})")
        print(f"   Cases : {len(test_cases)}   Judge : {'on' if use_judge else 'off'}\n")

    prov_a = get_provider(cfg_a["provider"], model=cfg_a["model"])
    prov_b = get_provider(cfg_b["provider"], model=cfg_b["model"])
    emb_prov = _resolve_embedder(cfg_a)
    if emb_prov is None and verbose:
        print("  ⚠ No embedding provider available, using TF-IDF fallback (offline mode).\n")

    judge_prov = None
    if use_judge:
        try:
            judge_prov = get_provider(judge_provider, model=judge_model)
        except Exception:
            if verbose:
                print("  ⚠ Judge provider unavailable, skipping judge.\n")

    report = DiffReport(
        version_a=str(cfg_a["version"]), version_b=str(cfg_b["version"]),
        model_a=cfg_a["model"], model_b=cfg_b["model"],
        total_cases=len(test_cases),
    )

    for i, case in enumerate(test_cases):
        if verbose:
            print(f"  [{i+1}/{len(test_cases)}] {case['id']}")

        inp = case["input"]
        params_a = {"temperature": cfg_a["temperature"], "max_tokens": cfg_a["max_tokens"]}
        params_b = {"temperature": cfg_b["temperature"], "max_tokens": cfg_b["max_tokens"]}

        prompt_a = cfg_a["prompt_template"].format(input=inp)
        prompt_b = cfg_b["prompt_template"].format(input=inp)

        resp_a = prov_a.complete(cfg_a["system"], prompt_a, **params_a)
        resp_b = prov_b.complete(cfg_b["system"], prompt_b, **params_b)

        sim = _embed_pair(emb_prov, resp_a.text, resp_b.text)
        drift = round(1.0 - sim, 4)
        level = classify_drift(sim)

        verdict, reasoning = None, None
        if judge_prov and level != "none":
            verdict, reasoning = _judge(
                judge_prov, inp, resp_a.text, resp_b.text,
                expected=case.get("expected"),
            )
            if verdict == "regressed":
                level = "regression"

        report.cases.append(CaseResult(
            case_id=case["id"],
            input=inp,
            output_a=resp_a.text,
            output_b=resp_b.text,
            similarity=round(sim, 4),
            drift_score=drift,
            drift_level=level,
            judge_verdict=verdict,
            judge_reasoning=reasoning,
            latency_a=resp_a.latency,
            latency_b=resp_b.latency,
            tokens_a=resp_a.input_tokens + resp_a.output_tokens,
            tokens_b=resp_b.input_tokens + resp_b.output_tokens,
        ))

    return report
