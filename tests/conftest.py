"""Shared test helpers. Nothing here touches the network or a paid API."""
import json

import pytest

from promptdrift.providers import BaseProvider, EmbedResponse, LLMResponse


class FakeProvider(BaseProvider):
    """Scripted provider. ``replies`` is a list (consumed in order) or a callable(system, prompt)."""

    def __init__(self, replies=None, vector=None, fail=False):
        self.replies = replies if replies is not None else ["ok"]
        self.vector = vector
        self.fail = fail
        self.calls = []

    def complete(self, system, prompt, **kwargs):
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        if self.fail:
            raise RuntimeError("boom")
        if callable(self.replies):
            text = self.replies(system, prompt)
        else:
            text = self.replies[(len(self.calls) - 1) % len(self.replies)]
        return LLMResponse(text=text, input_tokens=3, output_tokens=5, latency=0.1)

    def embed(self, text):
        if self.fail or self.vector is None:
            raise RuntimeError("no embeddings")
        return EmbedResponse(vector=self.vector(text) if callable(self.vector) else self.vector)


def verdict_json(v, why="because"):
    return json.dumps({"verdict": v, "reasoning": why})


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
