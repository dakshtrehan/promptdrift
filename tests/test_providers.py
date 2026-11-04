import json
import sys
import types

import pytest

from promptdrift.providers import (ANTHROPIC_LEGACY_API_VERSION, AnthropicProvider,
                                   OllamaProvider, OpenAIProvider,
                                   anthropic_supports_temperature, claude_version, get_provider)


@pytest.mark.parametrize("mid,ver,temp", [
    ("claude-haiku-4-5-20251001", (4, 5), True),
    ("claude-haiku-4-5", (4, 5), True),
    ("claude-sonnet-4-20250514", (4, 0), True),
    ("claude-opus-4-6", (4, 6), True),
    ("claude-opus-4-7", (4, 7), False),
    ("claude-sonnet-5", (5, 0), False),
    ("claude-haiku-5-5", (5, 5), False),
    ("claude-fable-5-1", (5, 1), False),
    ("claude-3-5-sonnet-20240620", (3, 5), True),
    ("claude-3-haiku-20240307", (3, 0), True),
    ("anthropic.claude-opus-4-7-v1:0", (4, 7), False),
    ("claude-3-5-sonnet@20240620", (3, 5), True),
    ("gpt-4o", None, False),
    ("my-finetune", None, False),
])
def test_claude_version_and_temperature(mid, ver, temp):
    assert claude_version(mid) == ver
    assert anthropic_supports_temperature(mid) is temp


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        if text is not None:
            self.text = text


class _FakeMessages:
    def __init__(self, content):
        self.content = content
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(
            content=self.content,
            usage=types.SimpleNamespace(input_tokens=7, output_tokens=9))


def _client(content):
    return types.SimpleNamespace(messages=_FakeMessages(content))


def test_anthropic_old_model_sends_temperature_with_legacy_header():
    client = _client([_Block("text", "hi")])
    p = AnthropicProvider("claude-haiku-4-5-20251001", client=client)
    r = p.complete("sys", "prompt", temperature=0.0, max_tokens=50, top_p=None)
    kw = client.messages.kwargs
    assert kw["extra_body"] == {"temperature": 0.0}
    assert kw["extra_headers"] == {"anthropic-version": ANTHROPIC_LEGACY_API_VERSION}
    assert "temperature" not in kw and "top_p" not in kw
    assert kw["max_tokens"] == 50 and kw["system"] == "sys"
    assert (r.text, r.input_tokens, r.output_tokens) == ("hi", 7, 9)


def test_anthropic_new_model_drops_sampling_and_skips_thinking():
    client = _client([_Block("thinking"), _Block("text", "Hello "), _Block("redacted_thinking"),
                      _Block("text", "world")])
    p = AnthropicProvider("claude-opus-4-7", client=client)
    r = p.complete("", "prompt", temperature=0.0, top_k=5)
    kw = client.messages.kwargs
    assert "extra_body" not in kw and "extra_headers" not in kw and "system" not in kw
    assert "temperature" not in kw and "top_k" not in kw
    assert kw["max_tokens"] == 1024
    assert r.text == "Hello world"


def test_anthropic_override_and_merge_extra():
    p = AnthropicProvider("my-proxy-model", client=_client([]), temperature_supported=True)
    req = p.build_request("s", "p", temperature=0.2, extra_body={"x": 1},
                          extra_headers={"h": "v"}, thinking={"type": "disabled"})
    assert req["extra_body"] == {"x": 1, "temperature": 0.2}
    assert req["extra_headers"] == {"h": "v", "anthropic-version": "2023-06-01"}
    assert req["thinking"] == {"type": "disabled"}
    r = p.complete("s", "p")
    assert r.text == "" and r.input_tokens == 7
    off = AnthropicProvider("claude-haiku-4-5", client=_client([]), temperature_supported=False)
    assert "extra_body" not in off.build_request("s", "p", temperature=0.5)


def test_anthropic_missing_usage():
    client = types.SimpleNamespace(messages=types.SimpleNamespace(
        create=lambda **kw: types.SimpleNamespace(content=None, usage=None)))
    r = AnthropicProvider("claude-opus-4-7", client=client).complete("s", "p")
    assert (r.text, r.input_tokens, r.output_tokens) == ("", 0, 0)


def test_anthropic_real_sdk_wire_format():
    """Drive the installed anthropic SDK against a mock transport: no network, no cost."""
    anthropic = pytest.importorskip("anthropic")
    try:  # anthropic 2.x ships on httpx2, 1.x on httpx
        import httpx2 as httpx
    except ImportError:
        httpx = pytest.importorskip("httpx")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
            "stop_reason": "end_turn", "stop_sequence": None,
            "content": [{"type": "thinking", "thinking": "hmm", "signature": "s"},
                        {"type": "text", "text": "4"}],
            "usage": {"input_tokens": 3, "output_tokens": 1}})

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    old = AnthropicProvider("claude-haiku-4-5-20251001", client=client)
    assert old.complete("s", "2+2?", temperature=0.0, max_tokens=5).text == "4"
    body = json.loads(seen[-1].content)
    assert body["temperature"] == 0.0
    assert seen[-1].headers["anthropic-version"] == "2023-06-01"

    new = AnthropicProvider("claude-opus-4-7", client=client)
    assert new.complete("s", "2+2?", temperature=0.0).text == "4"
    body = json.loads(seen[-1].content)
    assert "temperature" not in body
    assert seen[-1].headers["anthropic-version"] != "2023-06-01"  # SDK default


def test_anthropic_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ImportError, match="pip install anthropic"):
        AnthropicProvider()


def test_anthropic_embed_uses_openai(monkeypatch):
    calls = []

    class FakeOpenAI:
        def embed(self, text):
            calls.append(text)
            return "vec"
    monkeypatch.setattr("promptdrift.providers.OpenAIProvider", FakeOpenAI)
    p = AnthropicProvider("claude-opus-4-7", client=_client([]))
    assert p.embed("x") == "vec" and p.embed("y") == "vec" and calls == ["x", "y"]


def _fake_openai_module(monkeypatch):
    rec = {}

    class FakeOpenAI:
        def __init__(self, api_key=None):
            rec["api_key"] = api_key
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._chat))
            self.embeddings = types.SimpleNamespace(create=self._emb)

        def _chat(self, **kw):
            rec["chat"] = kw
            msg = types.SimpleNamespace(content=None)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=msg)],
                usage=types.SimpleNamespace(prompt_tokens=2, completion_tokens=3))

        def _emb(self, **kw):
            rec["emb"] = kw
            return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.1, 0.2])])

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    return rec


def test_openai_provider(monkeypatch):
    rec = _fake_openai_module(monkeypatch)
    p = get_provider("openai", model="gpt-x", api_key="k")
    r = p.complete("sys", "hi", temperature=0.0, max_tokens=5)
    assert rec["api_key"] == "k" and rec["chat"]["model"] == "gpt-x"
    assert rec["chat"]["messages"][0] == {"role": "system", "content": "sys"}
    assert (r.text, r.input_tokens, r.output_tokens) == ("", 2, 3)
    assert p.embed("t").vector == [0.1, 0.2]


def test_openai_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ImportError, match="pip install openai"):
        OpenAIProvider()


def test_ollama_provider(monkeypatch):
    posts = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fake_post(url, json):
        posts.append((url, json))
        if url.endswith("/api/chat"):
            return Resp({"message": {"content": "hey"}})
        return Resp({"embedding": [1.0]})
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=fake_post))
    p = get_provider("ollama", model="llama3.2", base_url="http://h:1")
    assert p.complete("s", "p", temperature=0.0, max_tokens=9).text == "hey"
    assert posts[0][0] == "http://h:1/api/chat"
    assert posts[0][1]["options"] == {"temperature": 0.0, "num_predict": 9}
    p.complete("s", "p")
    assert "options" not in posts[1][1]
    assert p.embed("t").vector == [1.0]


def test_get_provider_unknown():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nope")
