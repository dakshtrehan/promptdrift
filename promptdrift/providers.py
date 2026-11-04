"""
Provider abstraction: OpenAI, Anthropic, Ollama.
"""
from __future__ import annotations
import os
import re
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


#: API version that still accepts sampling parameters such as ``temperature``.
#: anthropic SDK 2.x defaults to a newer version that rejects them.
ANTHROPIC_LEGACY_API_VERSION = "2023-06-01"

_SAMPLING_PARAMS = ("temperature", "top_p", "top_k")
_CLAUDE_NEW = re.compile(
    r"claude-(?:haiku|sonnet|opus|fable)-(\d+)(?:-(\d{1,2}))?(?:[-@]\d{8}|-latest)?(?:$|[^\d])")
_CLAUDE_OLD = re.compile(r"claude-(\d+)(?:-(\d))?-(?:haiku|sonnet|opus)")


def claude_version(model: str) -> tuple[int, int] | None:
    """``(major, minor)`` of a Claude model id, or ``None`` if it is not recognised.

    Handles ``claude-haiku-4-5-20251001``, ``claude-opus-4-7``, ``claude-sonnet-4-20250514``,
    ``claude-3-5-sonnet-20240620`` and provider prefixes such as ``anthropic.`` or ``@date``.
    """
    m = _CLAUDE_NEW.search(model) or _CLAUDE_OLD.search(model)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def anthropic_supports_temperature(model: str) -> bool:
    """True for Claude models before 4.7, which accept sampling parameters.

    Claude 4.7 and later only accept default sampling. Unrecognised ids return
    False, because omitting ``temperature`` is accepted by every model.
    """
    v = claude_version(model)
    return v is not None and v < (4, 7)


class AnthropicProvider(BaseProvider):
    """Claude via the official ``anthropic`` SDK (1.x or 2.x).

    Sampling parameters are sent only when the model supports them, and then
    with ``anthropic-version: 2023-06-01``, because SDK 2.x defaults to an API
    version that rejects them. Only ``text`` blocks are read from the response,
    so thinking blocks are skipped.
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001",
                 api_key: str | None = None, temperature_supported: bool | None = None,
                 client=None):
        if client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError("pip install anthropic")
            client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.client = client
        self.model = model
        self.temperature_supported = (anthropic_supports_temperature(model)
                                      if temperature_supported is None else temperature_supported)
        self._embed: OpenAIProvider | None = None

    def _embedder(self) -> OpenAIProvider:
        if self._embed is None:
            self._embed = OpenAIProvider()
        return self._embed

    def build_request(self, system: str, prompt: str, **kwargs) -> dict:
        """Keyword arguments for ``client.messages.create``. Exposed for testing."""
        max_tokens = kwargs.pop("max_tokens", 1024)
        sampling = {k: kwargs.pop(k) for k in _SAMPLING_PARAMS
                    if k in kwargs and kwargs[k] is not None}
        for k in _SAMPLING_PARAMS:
            kwargs.pop(k, None)
        req = {"model": self.model, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": prompt}], **kwargs}
        if system:
            req["system"] = system
        if sampling and self.temperature_supported:
            extra_body = dict(req.pop("extra_body", None) or {})
            extra_body.update(sampling)
            headers = dict(req.pop("extra_headers", None) or {})
            headers["anthropic-version"] = ANTHROPIC_LEGACY_API_VERSION
            req["extra_body"] = extra_body
            req["extra_headers"] = headers
        return req

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        t0 = time.time()
        r = self.client.messages.create(**self.build_request(system, prompt, **kwargs))
        text = "".join(getattr(b, "text", "") or "" for b in (r.content or [])
                       if getattr(b, "type", "text") == "text")
        usage = getattr(r, "usage", None)
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
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
        options = {}
        if kwargs.get("temperature") is not None:
            options["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            options["num_predict"] = kwargs["max_tokens"]
        body = {"model": self.model, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}]}
        if options:
            body["options"] = options
        r = requests.post(f"{self.base_url}/api/chat", json=body)
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
