"""
LLM-as-judge: is output B improved, regressed or neutral compared with output A?

Works with any object that has ``complete(system, prompt, **kwargs)`` returning
something with a ``.text`` attribute, so every provider in
``promptdrift.providers`` (and any custom one) can act as the judge.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from .providers import SupportsComplete

__all__ = ["JUDGE_SYSTEM", "DEFAULT_JUDGE_MODELS", "build_judge_prompt", "parse_verdict",
           "judge_pair", "judge_both_orders", "default_judge_provider", "resolve_judge"]

VERDICTS = ("improved", "regressed", "neutral")
FLIP = {"improved": "regressed", "regressed": "improved", "neutral": "neutral"}

#: Judge model used for each provider when none is given.
DEFAULT_JUDGE_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "llama3.2",
}

JUDGE_SYSTEM = "You evaluate AI output quality. Return only valid JSON."


def build_judge_prompt(inp: str, out_a: str, out_b: str, expected: str | None = None) -> str:
    exp_line = f"\nExpected answer: {expected}" if expected else ""
    return f"""Compare two AI outputs for the same input.{exp_line}

Input: {inp}

Output A (baseline): {out_a}

Output B (new version): {out_b}

Is Output B improved, regressed, or neutral compared to A?
- improved: clearly more accurate, helpful, or complete
- regressed: less accurate, introduces errors, or loses important information
- neutral: equivalent quality, just phrased differently

Respond ONLY with this JSON (no markdown, no extra text):
{{"verdict": "improved|regressed|neutral", "reasoning": "one sentence"}}"""


def parse_verdict(text: str) -> tuple[str, str] | None:
    """Pull ``(verdict, reasoning)`` out of a judge reply, or ``None`` if it is unusable."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        return None
    return verdict, str(data.get("reasoning", ""))


def _ask(provider: SupportsComplete, inp: str, out_a: str, out_b: str,
         expected: str | None, max_tokens: int) -> tuple[str, str] | None:
    try:
        r = provider.complete(JUDGE_SYSTEM, build_judge_prompt(inp, out_a, out_b, expected),
                              max_tokens=max_tokens)
    except Exception:
        return None
    return parse_verdict(r.text)


def judge_pair(provider: SupportsComplete, inp: str, out_a: str, out_b: str,
               expected: str | None = None, max_tokens: int = 150) -> tuple[str, str]:
    """Ask ``provider`` to compare A against B once, in that order.

    Returns ``(verdict, reasoning)``. Any provider error or unparseable reply
    gives ``("neutral", "")``, matching v0.1.1.
    """
    return _ask(provider, inp, out_a, out_b, expected, max_tokens) or ("neutral", "")


def judge_both_orders(provider: SupportsComplete, inp: str, out_a: str, out_b: str,
                      expected: str | None = None, max_tokens: int = 150) -> dict[str, Any]:
    """Judge A vs B, then B vs A, to expose position bias.

    The second verdict is flipped back into A-vs-B terms. If the two agree the
    result is that verdict; if they disagree it is ``"inconsistent"``; if either
    call fails or returns unusable text it is ``"error"``.

    Returns ``{"verdict", "reasoning", "verdict_ab", "verdict_ba", "consistent"}``
    where ``verdict_ba`` is already flipped (``None`` for a failed order) and
    ``consistent`` is ``None`` when an order failed.
    """
    ab = _ask(provider, inp, out_a, out_b, expected, max_tokens)
    ba = _ask(provider, inp, out_b, out_a, expected, max_tokens)
    v_ab = ab[0] if ab else None
    v_ba = FLIP[ba[0]] if ba else None
    if ab is None or ba is None:
        return {"verdict": "error", "reasoning": "judge call failed or returned unusable text",
                "verdict_ab": v_ab, "verdict_ba": v_ba, "consistent": None}
    if v_ab == v_ba:
        return {"verdict": v_ab, "reasoning": ab[1], "verdict_ab": v_ab, "verdict_ba": v_ba,
                "consistent": True}
    return {"verdict": "inconsistent",
            "reasoning": f"A-first said {v_ab}, B-first said {v_ba}. {ab[1]}".strip(),
            "verdict_ab": v_ab, "verdict_ba": v_ba, "consistent": False}


def default_judge_provider() -> str | None:
    """Pick a judge provider from the environment.

    OpenAI when ``OPENAI_API_KEY`` is set (the v0.1.1 default), otherwise
    Anthropic when ``ANTHROPIC_API_KEY`` is set, otherwise ``None``.
    """
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def resolve_judge(judge_provider: Union[str, SupportsComplete, None] = None,
                  judge_model: str | None = None,
                  verbose: bool = False) -> Optional[SupportsComplete]:
    """Build the judge, or return ``None`` if none is available.

    ``judge_provider`` may be a provider object (anything with ``complete``),
    a provider name, or ``None`` to choose from the environment (see
    ``default_judge_provider``). ``judge_model`` defaults per provider
    (``DEFAULT_JUDGE_MODELS``).
    """
    if judge_provider is not None and not isinstance(judge_provider, str):
        return judge_provider
    from .providers import get_provider

    name = judge_provider or default_judge_provider()
    if name is None:
        if verbose:
            print("  ⚠ No judge available (set OPENAI_API_KEY or ANTHROPIC_API_KEY, "
                  "or pass --judge-provider). Skipping judge.\n")
        return None
    model = judge_model or DEFAULT_JUDGE_MODELS.get(name)
    try:
        return get_provider(name, **({"model": model} if model else {}))
    except Exception as e:
        if verbose:
            print(f"  ⚠ Judge provider '{name}' unavailable ({e}). Skipping judge.\n")
        return None
