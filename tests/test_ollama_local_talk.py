"""Talk's Ollama calls: no silent thinking, a capped reply, and a retry for an
Ollama that doesn't know the think option."""
import asyncio
import json

import httpx

from services import ollama_local


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(ollama_local.httpx, "AsyncClient", client)


def _collect(messages, model):
    async def run():
        return [d async for d in ollama_local.chat_stream(messages, model)]

    return asyncio.run(run())


def test_chat_turns_thinking_off_and_caps_the_reply(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        lines = [json.dumps({"message": {"content": "Hi there."}}), json.dumps({"done": True})]
        return httpx.Response(200, text="\n".join(lines))

    _patch_client(monkeypatch, handler)
    assert _collect([{"role": "user", "content": "hi"}], "qwen3:1.7b") == ["Hi there."]
    assert bodies[0]["think"] is False
    assert bodies[0]["options"]["num_predict"] == ollama_local.TALK_MAX_TOKENS


def test_an_ollama_without_think_support_is_asked_again(monkeypatch):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "think" in body:
            return httpx.Response(400, text='{"error":"invalid option think"}')
        return httpx.Response(200, text=json.dumps({"message": {"content": "Ok."}, "done": True}))

    _patch_client(monkeypatch, handler)
    assert _collect([{"role": "user", "content": "hi"}], "llama3.2:1b") == ["Ok."]
    assert len(bodies) == 2 and "think" not in bodies[1]


def test_warm_loads_the_model_and_never_raises(monkeypatch):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    _patch_client(monkeypatch, handler)
    asyncio.run(ollama_local.warm("gemma3:1b"))
    assert seen == [{"model": "gemma3:1b", "keep_alive": ollama_local.TALK_KEEP_ALIVE}]

    def broken(request):
        raise httpx.ConnectError("down")

    _patch_client(monkeypatch, broken)
    asyncio.run(ollama_local.warm("gemma3:1b"))  # no exception
