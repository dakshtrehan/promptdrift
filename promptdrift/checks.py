"""
Deterministic per-case checks: regex, JSON keys and exact match.

Checks run on both outputs with no model calls. A case passes when every check
passes. A pass in A that becomes a fail in B is a hard regression.

A case declares checks under ``checks``, either as a list::

    "checks": [
        {"type": "regex", "pattern": "\\\\b96\\\\b"},
        {"type": "json_keys", "keys": ["name", "age"]},
        {"type": "exact", "value": "96", "ignore_case": true}
    ]

or as a shorthand dict::

    "checks": {"regex": "\\\\b96\\\\b", "json_keys": ["name"], "exact": "96"}

``{"type": "exact"}`` with no ``value`` (or ``"exact": true``) compares against
the case's ``expected`` field.
"""
from __future__ import annotations

import json
import re

from .structure import strip_fence

__all__ = ["normalize_checks", "run_check", "run_checks", "check_flip"]

CHECK_TYPES = ("regex", "json_keys", "exact")
_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def normalize_checks(spec, expected: str | None = None) -> list[dict]:
    """Validate a checks spec and return it as a list of check dicts.

    Raises ``ValueError`` for unknown types, missing fields or a bad regex.
    """
    if spec is None:
        return []
    if isinstance(spec, dict):
        if "type" in spec:
            spec = [spec]
        else:
            items = []
            for key, val in spec.items():
                if key == "regex":
                    items.append({"type": "regex", "pattern": val})
                elif key == "json_keys":
                    items.append({"type": "json_keys", "keys": val})
                elif key == "exact":
                    items.append({"type": "exact"} if val is True
                                 else {"type": "exact", "value": val})
                else:
                    raise ValueError(f"Unknown check '{key}'. Use one of {list(CHECK_TYPES)}.")
            spec = items
    if not isinstance(spec, list):
        raise ValueError("'checks' must be a list or a dict.")

    out = []
    for raw in spec:
        if not isinstance(raw, dict) or raw.get("type") not in CHECK_TYPES:
            raise ValueError(f"Bad check {raw!r}: 'type' must be one of {list(CHECK_TYPES)}.")
        chk = dict(raw)
        t = chk["type"]
        if t == "regex":
            if not isinstance(chk.get("pattern"), str):
                raise ValueError("regex check needs a string 'pattern'.")
            flags = 0
            for ch in str(chk.get("flags", "")):
                if ch not in _FLAGS:
                    raise ValueError(f"Unknown regex flag '{ch}'. Use i, m or s.")
                flags |= _FLAGS[ch]
            try:
                re.compile(chk["pattern"], flags)
            except re.error as e:
                raise ValueError(f"Invalid regex {chk['pattern']!r}: {e}") from None
        elif t == "json_keys":
            keys = chk.get("keys")
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list) or not keys:
                raise ValueError("json_keys check needs a non-empty 'keys' list.")
            chk["keys"] = [str(k) for k in keys]
        else:  # exact
            if "value" not in chk:
                if expected is None:
                    raise ValueError("exact check has no 'value' and the case has no 'expected'.")
                chk["value"] = expected
            chk["value"] = str(chk["value"])
        out.append(chk)
    return out


def run_check(text: str, chk: dict) -> dict:
    """Run one normalized check. Returns ``{"type", "passed", "detail"}``."""
    t = chk["type"]
    if t == "regex":
        flags = 0
        for ch in str(chk.get("flags", "")):
            flags |= _FLAGS[ch]
        passed = re.search(chk["pattern"], text, flags) is not None
        return {"type": t, "passed": passed, "detail": chk["pattern"]}
    if t == "json_keys":
        body = strip_fence(text)[0]
        try:
            obj = json.loads(body)
        except ValueError:
            return {"type": t, "passed": False, "detail": "not valid JSON"}
        if not isinstance(obj, dict):
            return {"type": t, "passed": False, "detail": "JSON is not an object"}
        missing = [k for k in chk["keys"] if k not in obj]
        return {"type": t, "passed": not missing,
                "detail": f"missing {missing}" if missing else "all keys present"}
    # exact
    a, b = text, chk["value"]
    if chk.get("strip", True):
        a, b = a.strip(), b.strip()
    if chk.get("ignore_case", False):
        a, b = a.lower(), b.lower()
    return {"type": t, "passed": a == b, "detail": chk["value"]}


def run_checks(text: str, checks: list[dict]) -> dict:
    """Run all checks. Returns ``{"passed": bool, "results": [...]}``."""
    results = [run_check(text, c) for c in checks]
    return {"passed": all(r["passed"] for r in results), "results": results}


def check_flip(passed_a: bool | None, passed_b: bool | None) -> str | None:
    """``"pass_to_fail"``, ``"fail_to_pass"`` or ``None`` when nothing flipped."""
    if passed_a is None or passed_b is None or passed_a == passed_b:
        return None
    return "pass_to_fail" if passed_a else "fail_to_pass"
