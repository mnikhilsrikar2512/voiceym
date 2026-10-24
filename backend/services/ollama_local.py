"""Talk's language model, served by a local Ollama.

Talk lets the user pick any model installed in Ollama (``ollama pull <name>``
adds one). Replies go through Ollama's native ``/api/chat`` rather than its
OpenAI-compatible endpoint so the request can carry a short context window:
``num_ctx`` sizes the KV cache Ollama allocates next to the weights, and a
spoken conversation never needs Ollama's default window.
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

#: Context window for a spoken conversation (the agent keeps a short history).
TALK_NUM_CTX = 2048
#: How long Ollama keeps the model loaded after the last reply.
TALK_KEEP_ALIVE = "10m"
#: Preferred defaults, smallest first: the first one installed wins.
PREFERRED_MODELS = ("llama3.2:1b", "qwen2.5:1.5b", "gemma3:1b", "llama3.2:3b")
#: For Indian languages: Gemma 3 is trained on many more languages than Llama 3.2.
MULTILINGUAL_MODELS = ("gemma3:4b", "gemma3:1b", "qwen2.5:3b")


def base_url() -> str:
    """The Ollama server root (no ``/v1``), from the Ollama provider settings."""
    url = ""
    try:
        from services import llm_providers

        provider = llm_providers.get_provider("ollama")
        if provider is not None:
            url = llm_providers.resolve_base_url(provider)
    except Exception:  # noqa: BLE001 -- settings unavailable: use the default
        url = ""
    url = (url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    return url[: -len("/v1")] if url.endswith("/v1") else url


def pick_default(names: list[str], preferred_models: tuple[str, ...] = PREFERRED_MODELS) -> str:
    """The first preferred model that is installed, else the first listed."""
    for preferred in preferred_models:
        for name in names:
            if name == preferred or name == preferred + ":latest":
                return name
    return names[0] if names else ""


async def list_models() -> dict:
    """Installed models, smallest first, plus the default Talk should use."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(base_url() + "/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {"available": False, "models": [], "default": "", "multilingual_default": ""}
    models = [
        {"name": str(item.get("name") or ""), "size": int(item.get("size") or 0)}
        for item in payload.get("models") or []
        if item.get("name")
    ]
    models.sort(key=lambda item: item["size"])
    names = [item["name"] for item in models]
    return {
        "available": True,
        "models": models,
        "default": pick_default(names),
        "multilingual_default": pick_default(names, MULTILINGUAL_MODELS),
    }


#: A spoken reply is a few sentences; capping it keeps a rambling model fast.
TALK_MAX_TOKENS = 220


async def warm(model: str) -> None:
    """Load ``model`` into memory now (an empty generate), so the first reply
    doesn't wait for it. Best-effort: a failure only means a slower first turn."""
    if not model:
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=180.0)) as client:
            await client.post(base_url() + "/api/generate",
                              json={"model": model, "keep_alive": TALK_KEEP_ALIVE})
    except httpx.HTTPError:
        pass  # Ollama is down or slow; the first reply loads the model instead.


async def chat_stream(messages: list[dict], model: str) -> AsyncIterator[str]:
    """Stream a reply from ``model`` as text deltas."""
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": TALK_KEEP_ALIVE,
        # Reasoning models (Qwen3, …) otherwise think silently before every
        # spoken reply, which is most of the wait; Talk needs a direct answer.
        "think": False,
        "options": {"num_ctx": TALK_NUM_CTX, "temperature": 0.4, "num_predict": TALK_MAX_TOKENS},
    }
    timeout = httpx.Timeout(10.0, read=120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response_cm = client.stream("POST", base_url() + "/api/chat", json=body)
        response = await response_cm.__aenter__()
        if response.status_code >= 400:
            detail = (await response.aread()).decode("utf-8", "replace")[:200]
            await response_cm.__aexit__(None, None, None)
            if "think" not in detail.lower():
                raise RuntimeError(f"Ollama returned {response.status_code}: {detail}")
            # An Ollama or model without the think option: ask again without it.
            body.pop("think")
            response_cm = client.stream("POST", base_url() + "/api/chat", json=body)
            response = await response_cm.__aenter__()
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", "replace")[:200]
                await response_cm.__aexit__(None, None, None)
                raise RuntimeError(f"Ollama returned {response.status_code}: {detail}")
        try:
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(f"Ollama: {chunk['error']}")
                text = (chunk.get("message") or {}).get("content")
                if text:
                    yield text
                if chunk.get("done"):
                    return
        finally:
            await response_cm.__aexit__(None, None, None)
