"""Talk to the voice agent over a WebSocket, from any client.

``WS /v1/agent/ws`` runs one :class:`services.voice_agent.VoiceAgentSession`
per connection. ``GET /agent`` serves a single-page browser client for it.

Protocol:
    → {"type": "session.start", "instructions": "...", "voice": "<profile id>",
       "engine": "", "language": "en", "greet": false, "barge_in": true}
         Must be the first message. Every field is optional.
    → binary frames: microphone audio, PCM16 little-endian mono at 16 kHz
    → {"type": "input_text", "text": "..."}     a typed message
    → {"type": "interrupt"}                     stop the current reply
    → {"type": "session.end"}                   close the session

    ← {"type": "session.ready", "input_sample_rate": 16000, "output_sample_rate": 24000}
    ← {"type": "state", "state": "listening" | "thinking" | "speaking"}
    ← {"type": "input.speech_started"} / {"type": "input.speech_stopped"}
    ← {"type": "transcript", "role": "user" | "assistant", "text": "...",
       "interrupted": true?}
    ← {"type": "transcript.delta", "role": "assistant", "text": "<sentence>"}
    ← binary frames: the agent's voice, PCM16 little-endian mono at 24 kHz
    ← {"type": "audio.clear", "reason": "..."}  drop any audio not yet played
    ← {"type": "response.done"}
    ← {"type": "error", "code": "...", "message": "..."}

Access follows the live transcription socket: loopback clients, or remote
clients presenting the backend's API key; browser pages only from an allowed
origin.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from api.dependencies import is_local_host, ws_remote_authorized
from services import voice_agent

router = APIRouter()
logger = logging.getLogger("omnivoice.voice_agent.ws")

AGENT_WS_PATH = "/v1/agent/ws"
_CLIENT_PAGE = Path(__file__).resolve().parents[2] / "assets" / "agent" / "index.html"
#: Largest single audio frame accepted (about 2 s of 16 kHz PCM16).
MAX_AUDIO_FRAME_BYTES = 64 * 1024


@router.get("/agent", response_class=HTMLResponse, include_in_schema=False)
def agent_page() -> HTMLResponse:
    """The browser client: talk to the agent with your microphone."""
    return HTMLResponse(_CLIENT_PAGE.read_text(encoding="utf-8"))


@router.websocket(AGENT_WS_PATH)
async def agent_ws(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin:
        from core.csrf import origin_allowed

        if not origin_allowed(websocket):
            await websocket.close(code=1008, reason="browser origin not allowed")
            return
    host = websocket.client.host if websocket.client else None
    if not is_local_host(host) and not ws_remote_authorized(websocket):
        await websocket.close(code=1008, reason="loopback origin required")
        return

    await websocket.accept()

    send_lock = asyncio.Lock()

    async def send(event) -> None:
        async with send_lock:
            try:
                if isinstance(event, bytes):
                    await websocket.send_bytes(event)
                else:
                    await websocket.send_text(json.dumps(event))
            except (WebSocketDisconnect, RuntimeError):
                pass  # the receive loop notices the disconnect and closes the session

    session = None
    try:
        first = await websocket.receive()
        data = _json(first)
        if data is None or data.get("type") != "session.start":
            await send({"type": "error", "code": "bad_request",
                        "message": "Send {\"type\": \"session.start\"} first."})
            await websocket.close(code=1008)
            return
        session = voice_agent.VoiceAgentSession(voice_agent.SessionConfig.from_message(data), send)
        await session.start()
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            chunk = message.get("bytes")
            if chunk is not None:
                if len(chunk) <= MAX_AUDIO_FRAME_BYTES:
                    await session.push_audio(chunk)
                continue
            data = _json(message)
            if data is None:
                continue
            kind = data.get("type")
            if kind == "input_text":
                await session.push_text(str(data.get("text") or ""))
            elif kind == "interrupt":
                await session.interrupt(reason="client")
            elif kind == "session.end":
                break
    except WebSocketDisconnect:
        pass  # The client hung up; the finally block cleans up.
    finally:
        if session is not None:
            await session.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass  # Already closed by the client or by Starlette.


def _json(message: dict) -> dict | None:
    text = message.get("text")
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
