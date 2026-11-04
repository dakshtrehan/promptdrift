"""
LLM-as-judge: is output B improved, regressed or neutral compared with output A?

Works with any object that has ``complete(system, prompt, **kwargs)`` returning
something with a ``.text`` attribute, so every provider in
``promptdrift.providers`` (and any custom one) can act as the judge.
"""
from __future__ import annotations

import json

__all__ = ["JUDGE_SYSTEM", "build_judge_prompt", "parse_verdict", "judge_pair"]

VERDICTS = ("improved", "regressed", "neutral")

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


def judge_pair(provider, inp: str, out_a: str, out_b: str,
               expected: str | None = None, max_tokens: int = 150) -> tuple[str, str]:
    """Ask ``provider`` to compare A against B once, in that order.

    Returns ``(verdict, reasoning)``. Any provider error or unparseable reply
    gives ``("neutral", "")``, matching v0.1.1.
    """
    try:
        r = provider.complete(JUDGE_SYSTEM, build_judge_prompt(inp, out_a, out_b, expected),
                              max_tokens=max_tokens)
        parsed = parse_verdict(r.text)
        if parsed:
            return parsed
    except Exception:
        pass
    return "neutral", ""
