"""
Provider abstraction — OpenAI, Anthropic, Ollama.
"""
from __future__ import annotations
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency: float = 0.0
    model: str = ""


@dataclass
class EmbedResponse:
    vector: list[float]
    model: str = ""


class BaseProvider(ABC):
    @abstractmethod
    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        pass

    @abstractmethod
    def embed(self, text: str) -> EmbedResponse:
        pass


class OpenAIProvider(BaseProvider):
    def __init__(self, model: str = "gpt-4o-mini",
                 embed_model: str = "text-embedding-3-small",
                 api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("pip install openai")
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.model = model
        self.embed_model = embed_model

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        t0 = time.time()
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            **kwargs,
        )
        return LLMResponse(
            text=r.choices[0].message.content or "",
            input_tokens=r.usage.prompt_tokens,
            output_tokens=r.usage.completion_tokens,
            latency=round(time.time() - t0, 3),
            model=self.model,
        )

    def embed(self, text: str) -> EmbedResponse:
        r = self.client.embeddings.create(model=self.embed_model, input=text)
        return EmbedResponse(vector=r.data[0].embedding, model=self.embed_model)


class AnthropicProvider(BaseProvider):
    def __init__(self, model: str = "claude-haiku-4-5-20251001",
                 api_key: str | None = None):
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic")
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self._embed: OpenAIProvider | None = None

    def _embedder(self) -> OpenAIProvider:
        if self._embed is None:
            self._embed = OpenAIProvider()
        return self._embed

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        t0 = time.time()
        max_tokens = kwargs.pop("max_tokens", 1024)
        r = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}], **kwargs,
        )
        return LLMResponse(
            text=r.content[0].text,
            input_tokens=r.usage.input_tokens,
            output_tokens=r.usage.output_tokens,
            latency=round(time.time() - t0, 3),
            model=self.model,
        )

    def embed(self, text: str) -> EmbedResponse:
        return self._embedder().embed(text)


class OllamaProvider(BaseProvider):
    def __init__(self, model: str = "llama3.2",
                 base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        import requests
        t0 = time.time()
        r = requests.post(f"{self.base_url}/api/chat", json={
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        })
        r.raise_for_status()
        return LLMResponse(
            text=r.json()["message"]["content"],
            latency=round(time.time() - t0, 3),
            model=self.model,
        )

    def embed(self, text: str) -> EmbedResponse:
        import requests
        r = requests.post(f"{self.base_url}/api/embeddings",
                          json={"model": self.model, "prompt": text})
        r.raise_for_status()
        return EmbedResponse(vector=r.json()["embedding"], model=self.model)


def get_provider(name: str, **kwargs) -> BaseProvider:
    mapping = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "ollama": OllamaProvider}
    if name not in mapping:
        raise ValueError(f"Unknown provider '{name}'. Choose from: {list(mapping)}")
    return mapping[name](**kwargs)
