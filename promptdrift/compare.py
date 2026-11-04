"""
Main compare engine. Runs prompt versions, embeds outputs, scores drift.
"""
from __future__ import annotations
import json
from pathlib import Path

import yaml

from .core import DiffReport
from .judge import judge_pair, resolve_judge
from .providers import BaseProvider, get_provider
from .scoring import score_case
from .similarity import text_similarity, tfidf_similarity


# ── Config / case loaders ─────────────────────────────────────────────────────
def load_config(path: str | Path) -> dict:
    """
    Load a prompt config (YAML or JSON).

    Required fields: version, model, provider, prompt_template
    Optional: system, temperature, max_tokens, embedding_model, provider_options
    (extra keyword arguments for the provider, for example
    ``{"temperature_supported": false}`` or ``{"base_url": "..."}`` for Ollama)

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


# ── Backward-compatible aliases (v0.1.1 private names) ───────────────────────
# Kept so code written against v0.1.1 keeps working. New code should use
# promptdrift.similarity.tfidf_similarity and promptdrift.judge.judge_pair.
def _tfidf_similarity(text_a: str, text_b: str) -> float:
    return tfidf_similarity(text_a, text_b)


def _embed_pair(emb_prov: BaseProvider | None, text_a: str, text_b: str) -> float:
    return text_similarity(text_a, text_b, emb_prov)[0]


def _judge(provider: BaseProvider, inp: str, out_a: str, out_b: str,
           expected: str | None = None) -> tuple[str, str]:
    return judge_pair(provider, inp, out_a, out_b, expected=expected)


def render_prompt(template: str, inp: str) -> str:
    """Fill ``{input}`` in a prompt template.

    Uses ``str.format`` like v0.1.1, and falls back to plain replacement when the
    template contains other literal braces (for example a JSON example).
    """
    try:
        return template.format(input=inp)
    except (KeyError, IndexError, ValueError):
        return template.replace("{input}", inp)


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


def cfg_embedding_requested(*configs) -> bool:
    """True if any already-loaded config dict sets ``embedding_model``."""
    return any(isinstance(c, dict) and c.get("embedding_model") for c in configs)


# ── Main compare function ─────────────────────────────────────────────────────
def compare(
    config_a: str | Path | dict,
    config_b: str | Path | dict,
    cases: str | Path | list[dict],
    *,
    use_judge: bool = True,
    judge_provider: str | BaseProvider | None = None,
    judge_model: str | None = None,
    verbose: bool = True,
    checks=None,
    samples: int = 1,
    bootstrap: int = 1000,
    seed: int = 0,
    judge_both_orders: bool = True,
    similarity: str | None = None,
) -> DiffReport:
    """
    Compare two prompt versions across a set of test cases.

    Returns a DiffReport with per-case semantic drift, judge verdicts,
    latency deltas, structural drift, check flips, and an overall regression
    summary. ``checks`` is a default checks spec for cases without their own.
    ``samples`` > 1 calls each version that many times per case and reports
    within-version drift (noise floor) and excess drift. ``bootstrap`` sets the
    resamples for 95 percent CIs (0 disables); ``seed`` makes them reproducible.

    The judge is any provider name or provider object; with ``None`` it is
    chosen from the environment (OpenAI if ``OPENAI_API_KEY`` is set, else
    Anthropic if ``ANTHROPIC_API_KEY`` is set). It runs in both orders and
    marks disagreements as ``inconsistent`` unless ``judge_both_orders=False``.

    ``similarity`` is ``"tfidf"`` (free, offline, deterministic) or
    ``"embedding"``. When ``None`` it is ``"embedding"`` only if a config sets
    ``embedding_model``, otherwise ``"tfidf"``.
    """
    if samples < 1:
        raise ValueError("samples must be at least 1")
    cfg_a = load_config(config_a) if not isinstance(config_a, dict) else config_a
    cfg_b = load_config(config_b) if not isinstance(config_b, dict) else config_b
    test_cases = load_cases(cases) if not isinstance(cases, list) else cases
    if similarity is None:
        similarity = ("embedding" if cfg_embedding_requested(cfg_a, cfg_b) else "tfidf")
    if similarity not in ("tfidf", "embedding"):
        raise ValueError("similarity must be 'tfidf' or 'embedding'")

    if verbose:
        print(f"\n⚡ promptdrift")
        print(f"   A : {cfg_a['version']} ({cfg_a['provider']} / {cfg_a['model']})")
        print(f"   B : {cfg_b['version']} ({cfg_b['provider']} / {cfg_b['model']})")
        print(f"   Cases : {len(test_cases)}   Judge : {'on' if use_judge else 'off'}"
              f"   Samples : {samples}\n")

    prov_a = get_provider(cfg_a["provider"], model=cfg_a["model"],
                          **(cfg_a.get("provider_options") or {}))
    prov_b = get_provider(cfg_b["provider"], model=cfg_b["model"],
                          **(cfg_b.get("provider_options") or {}))
    emb_prov = _resolve_embedder(cfg_a) if similarity == "embedding" else None
    if similarity == "embedding" and emb_prov is None and verbose:
        print("  ⚠ No embedding provider available, using TF-IDF similarity instead.\n")

    judge_prov = resolve_judge(judge_provider, judge_model, verbose) if use_judge else None

    report = DiffReport(
        version_a=str(cfg_a["version"]), version_b=str(cfg_b["version"]),
        model_a=cfg_a["model"], model_b=cfg_b["model"],
        total_cases=len(test_cases),
        n_bootstrap=bootstrap, bootstrap_seed=seed,
        similarity="embedding" if emb_prov is not None else "tfidf",
    )

    for i, case in enumerate(test_cases):
        if verbose:
            print(f"  [{i+1}/{len(test_cases)}] {case['id']}")

        inp = case["input"]
        params_a = {"temperature": cfg_a["temperature"], "max_tokens": cfg_a["max_tokens"]}
        params_b = {"temperature": cfg_b["temperature"], "max_tokens": cfg_b["max_tokens"]}

        prompt_a = render_prompt(cfg_a["prompt_template"], inp)
        prompt_b = render_prompt(cfg_b["prompt_template"], inp)
        resps_a = [prov_a.complete(cfg_a["system"], prompt_a, **params_a) for _ in range(samples)]
        resps_b = [prov_b.complete(cfg_b["system"], prompt_b, **params_b) for _ in range(samples)]

        texts_a = [r.text for r in resps_a]
        texts_b = [r.text for r in resps_b]
        report.cases.append(score_case(
            case, texts_a if samples > 1 else texts_a[0], texts_b if samples > 1 else texts_b[0],
            judge=judge_prov, embedder=emb_prov,
            latency_a=round(sum(r.latency for r in resps_a) / samples, 3),
            latency_b=round(sum(r.latency for r in resps_b) / samples, 3),
            tokens_a=sum(r.input_tokens + r.output_tokens for r in resps_a),
            tokens_b=sum(r.input_tokens + r.output_tokens for r in resps_b),
            default_checks=checks, judge_both_orders=judge_both_orders,
        ))

    return report
