"""
Structural drift: did the *shape* of the output change?

Semantic similarity can stay high while the format changes in a way that
breaks a downstream parser (JSON gains a markdown fence, a list turns into
prose). These helpers measure that, with no model calls.
"""
from __future__ import annotations

import json
import re

__all__ = ["format_signature", "signature_string", "is_valid_json", "length_ratio",
           "structural_diff"]

_FENCE = re.compile(r"^```[\w+-]*[ \t]*\n(.*?)\n?```$", re.DOTALL)
_LIST_ITEM = re.compile(r"^\s*([-*+•]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_JSON_TYPES = {dict: "object", list: "array", str: "string", bool: "boolean",
               int: "number", float: "number", type(None): "null"}


def _strip_fence(text: str) -> tuple[str, bool]:
    t = text.strip()
    m = _FENCE.match(t)
    if m:
        return m.group(1), True
    return t, False


def _json_type(body: str) -> str | None:
    if not body:
        return None
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    return _JSON_TYPES.get(type(obj), "other")


def is_valid_json(text: str) -> bool:
    """True if ``text`` parses as JSON, allowing one surrounding markdown fence."""
    return _json_type(_strip_fence(text)[0]) is not None


def format_signature(text: str) -> dict:
    """Describe the surface format of an output.

    Keys: ``fence`` (wrapped in a markdown code fence), ``json_type`` (``object``,
    ``array``, ``string``, ``number``, ``boolean``, ``null`` or ``None`` when not
    JSON), ``list`` (every non-blank line is a bullet or numbered item),
    ``headings`` (has a markdown heading) and ``line_count`` (non-blank lines).
    """
    body, fenced = _strip_fence(text)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    jt = _json_type(body)
    return {
        "fence": fenced,
        "json_type": jt,
        "list": jt is None and bool(lines) and all(_LIST_ITEM.match(ln) for ln in lines),
        "headings": jt is None and bool(_HEADING.search(body)),
        "line_count": len(lines),
    }


def _line_bucket(n: int) -> str:
    if n <= 1:
        return "1"
    if n <= 4:
        return "2-4"
    return "5+"


def signature_string(sig_or_text) -> str:
    """Compact, comparable form of a signature, for example ``fence+json:object+lines:5+``.

    Line count is bucketed (1, 2-4, 5+) so that one extra sentence does not count
    as a format change. Accepts a signature dict or raw text.
    """
    sig = format_signature(sig_or_text) if isinstance(sig_or_text, str) else sig_or_text
    if sig["line_count"] == 0:
        return "empty"
    parts = []
    if sig["fence"]:
        parts.append("fence")
    if sig["json_type"]:
        parts.append(f"json:{sig['json_type']}")
    if sig["list"]:
        parts.append("list")
    if sig["headings"]:
        parts.append("headings")
    parts.append(f"lines:{_line_bucket(sig['line_count'])}")
    return "+".join(parts)


def length_ratio(text_a: str, text_b: str) -> float | None:
    """``len(b) / len(a)`` in characters (whitespace-trimmed).

    1.0 when both are empty, ``None`` when only A is empty (the ratio is undefined).
    """
    la, lb = len(text_a.strip()), len(text_b.strip())
    if la == 0:
        return 1.0 if lb == 0 else None
    return round(lb / la, 4)


def structural_diff(text_a: str, text_b: str) -> dict:
    """Structural comparison of two outputs.

    Returns ``signature_a``, ``signature_b``, ``format_changed``, ``json_valid_a``,
    ``json_valid_b``, ``json_validity_changed`` and ``length_ratio``.
    """
    sa, sb = signature_string(text_a), signature_string(text_b)
    ja, jb = is_valid_json(text_a), is_valid_json(text_b)
    return {
        "signature_a": sa,
        "signature_b": sb,
        "format_changed": sa != sb,
        "json_valid_a": ja,
        "json_valid_b": jb,
        "json_validity_changed": ja != jb,
        "length_ratio": length_ratio(text_a, text_b),
    }
