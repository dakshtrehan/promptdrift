"""Run the live ``compare()`` code path with a scripted fake provider.

No API key is needed and no network call is made. This is the same code path
that calls OpenAI or Anthropic in production, with the provider swapped for a
scripted one, so every live-mode field (latency, tokens, judge verdicts in both
orders) is filled in.

Run it from the repository root:

    python examples/live/mock_provider.py

How the provider is swapped:

* The judge accepts any object with a ``complete(system, prompt, **kwargs)``
  method, so ``judge_provider=ScriptedJudge()`` needs no patching.
* The generation providers are built by ``promptdrift.compare.get_provider``
  from the ``provider`` field of each config. promptdrift only knows
  ``openai``, ``anthropic`` and ``ollama``, so this script patches that
  function with ``unittest.mock.patch.object`` for the duration of the call.
  In a test suite you would do the same with ``monkeypatch``.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

from promptdrift import compare, print_report
from promptdrift.providers import BaseProvider, EmbedResponse, LLMResponse

HERE = Path(__file__).resolve().parent

# What each prompt version "says" for each test case. Keyed by a word that
# appears in the test case input, so the same provider class serves both sides.
ANSWERS = {
    "v1": {
        "capital": "Paris.",
        "15%": "30.",
        "train": "150 miles.",
        "Summarize": "A pangram sentence about a fox and a dog.",
        "reverse": "s[::-1]",
    },
    "v2": {
        "capital": "The capital of France is Paris.",
        "15%": "15% of 200 is 30.",
        "train": "The train travels 60 x 2.5 = 150 miles.",
        "Summarize": "The sentence is a pangram: it uses every letter of the alphabet.",
        "reverse": "```python\ns[::-1]\n```",
    },
}


class ScriptedProvider(BaseProvider):
    """Returns a canned answer per test case for one prompt version."""

    def __init__(self, version: str, model: str = "scripted"):
        self.version = version
        self.model = model
        self.calls = 0

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        self.calls += 1
        answers = ANSWERS[self.version]
        text = next((a for key, a in answers.items() if key in prompt), "I do not know.")
        return LLMResponse(text=text, input_tokens=len(prompt.split()),
                           output_tokens=len(text.split()), latency=0.01, model=self.model)

    def embed(self, text: str) -> EmbedResponse:  # not used: similarity stays TF-IDF
        raise RuntimeError("scripted provider has no embeddings")


class ScriptedJudge:
    """A judge that likes longer answers. Any object with ``complete`` works."""

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        # The judge prompt contains both outputs; call the longer one "improved".
        # The rule is position independent, so both orders agree and the
        # report shows consistent verdicts. Swap it for something order
        # dependent and the inconsistency rate goes up: that is the point of
        # judging in both orders.
        a = prompt.split("Output A (baseline): ", 1)[-1]
        a, rest = a.split("\n\nOutput B (new version): ", 1)
        b = rest.split("\n\nIs Output B ", 1)[0]
        if abs(len(a) - len(b)) < 8:
            verdict = "neutral"
        elif len(b) > len(a):
            verdict = "improved"
        else:
            verdict = "regressed"
        reply = json.dumps({"verdict": verdict, "reasoning": "scripted judge, length heuristic"})
        return LLMResponse(text=reply, input_tokens=10, output_tokens=5, latency=0.0)


def fake_get_provider(name: str, **kwargs) -> BaseProvider:
    """Stand-in for ``promptdrift.compare.get_provider``. Ignores the provider name."""
    return ScriptedProvider(version=kwargs.get("model", "v1"))


def main() -> int:
    # Keys are blanked so nothing can reach a real provider by accident.
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["ANTHROPIC_API_KEY"] = ""

    # The scripted provider picks its answers from ``model``, so point each
    # config's model at a version name instead of a real model id.
    # (Config dicts get the same defaults as load_config since 0.3.0; the
    # explicit temperature and max_tokens below just make the run obvious.)
    cfg_a = {"version": "v1", "provider": "openai", "model": "v1",
             "system": "You are a concise assistant.", "prompt_template": "{input}",
             "temperature": 0.0, "max_tokens": 256}
    cfg_b = {"version": "v2", "provider": "openai", "model": "v2",
             "system": "You are a helpful assistant.",
             "prompt_template": "Please answer the following: {input}",
             "temperature": 0.0, "max_tokens": 256}

    # ``promptdrift.compare`` is both a module and the ``compare`` function
    # re-exported by the package, so patch the module object explicitly rather
    # than by dotted string: that works the same on every Python from 3.9.
    compare_module = importlib.import_module("promptdrift.compare")
    with mock.patch.object(compare_module, "get_provider", fake_get_provider):
        report = compare(cfg_a, cfg_b, cases=str(HERE / "golden.json"),
                         judge_provider=ScriptedJudge(), verbose=False)

    print_report(report)
    summary = report.to_dict()["summary"]
    print("mode:", report.mode)
    print("summary:", json.dumps({k: summary[k] for k in ("avg_drift", "regressions",
                                                           "improvements", "neutral")}))
    print("judge:", json.dumps(summary["judge"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
