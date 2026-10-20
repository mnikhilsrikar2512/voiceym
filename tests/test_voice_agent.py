"""Voice agent: the transport-free conversation loop and its WebSocket.

No models anywhere: the LLM, ASR and TTS are scripted fakes injected through
``voice_agent.deps_factory``, and audio is synthetic tones and silence.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio
import json

import numpy as np
import pytest

from services import voice_agent as va


def _tone(seconds, amp=0.3, freq=300.0, sr=16000):
    t = np.arange(int(sr * seconds)) / sr
    return np.round(amp * 32767 * np.sin(2 * np.pi * freq * t)).astype("<i2").tobytes()


def _silence(seconds, sr=16000):
    return np.zeros(int(sr * seconds), dtype="<i2").tobytes()


class Fakes:
    def __init__(self):
        self.replies: list[str] = []
        self.heard: list[str] = []
        self.rendered: list[str] = []
        self.llm_calls: list[list[dict]] = []
        self.render_gate: asyncio.Event | None = None

    def deps(self):
        async def llm_stream(messages):
            self.llm_calls.append(messages)
            reply = self.replies.pop(0) if self.replies else "Okay."
            for i in range(0, len(reply), 5):
                yield reply[i:i + 5]
                await asyncio.sleep(0)

        async def transcribe(pcm, language):
            assert pcm.dtype == np.int16 and pcm.size > 0
            return self.heard.pop(0) if self.heard else ""

        async def render(text, **kw):
            self.rendered.append(text)
            if self.render_gate is not None:
                await self.render_gate.wait()
            yield np.zeros(2400, dtype=np.int16)  # 100 ms at 24 kHz

        return va.VoiceAgentDeps(llm_stream=llm_stream, transcribe=transcribe, render=render)


class Sink:
    def __init__(self):
        self.events: list = []

    async def __call__(self, event):
        self.events.append(event)

    def of(self, kind):
        return [e for e in self.events if isinstance(e, dict) and e.get("type") == kind]

    def audio(self):
        return [e for e in self.events if isinstance(e, bytes)]


async def _settle(session, rounds=50):
    for _ in range(rounds):
        await asyncio.sleep(0)
        turn = session._turn
        if (turn is None or turn.done()) and session._asr_q.empty():
            await asyncio.sleep(0)
            return
    await asyncio.sleep(0.05)


def test_think_filter_hides_reasoning_and_passes_plain_text():
    f = va.ThinkFilter()
    out = "".join(f.feed(d) for d in ["<thi", "nk>secret plan</th", "ink> Hello", " there."]) + f.finish()
    assert out == "Hello there."
    f = va.ThinkFilter()
    assert "".join(f.feed(d) for d in ["Hi", " you."]) + f.finish() == "Hi you."
    f = va.ThinkFilter()
    assert f.feed("<reasoning>never closes") == "" and f.finish() == ""


def test_system_prompt_keeps_the_users_instructions_and_speech_rules():
    prompt = va.system_prompt("You are a pizza ordering assistant.", "hi")
    assert prompt.startswith("You are a pizza ordering assistant.")
    assert "Speak hi." in prompt and "markdown" in prompt
    assert "language the user speaks" in va.system_prompt("", "auto")


def test_session_config_is_bounded_and_defaults_sensibly():
    cfg = va.SessionConfig.from_message({"instructions": "x" * 10000, "voice": 7, "greet": True})
    assert len(cfg.instructions) == va.MAX_INSTRUCTIONS_CHARS
    assert cfg.voice == "" and cfg.greet is True and cfg.barge_in is True
    assert va.SessionConfig.from_message({}).instructions == va.DEFAULT_INSTRUCTIONS


def test_text_turn_streams_sentences_audio_and_transcripts():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.replies = ["<think>plan</think>Sure thing. The answer is four."]
        session = va.VoiceAgentSession(va.SessionConfig(instructions="Be brief."), sink, fakes.deps())
        await session.start()
        await session.push_text("What is two plus two?")
        await _settle(session)
        await session.close()
        return fakes, sink

    fakes, sink = asyncio.run(run())
    assert sink.of("session.ready")[0]["output_sample_rate"] == 24000
    assert [e["text"] for e in sink.of("transcript")] == [
        "What is two plus two?", "Sure thing. The answer is four."]
    assert fakes.rendered == ["Sure thing.", "The answer is four."]
    assert len(sink.audio()) == 2 and all(len(a) == 4800 for a in sink.audio())
    assert [e["state"] for e in sink.of("state")] == ["thinking", "speaking", "listening"]
    assert sink.of("response.done")
    system = fakes.llm_calls[0][0]
    assert system["role"] == "system" and system["content"].startswith("Be brief.")
    assert fakes.llm_calls[0][-1] == {"role": "user", "content": "What is two plus two?"}


def test_spoken_utterance_is_transcribed_and_answered():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.heard = ["Hello agent"]
        fakes.replies = ["Hi! How can I help?"]
        session = va.VoiceAgentSession(va.SessionConfig(), sink, fakes.deps())
        await session.start()
        await session.push_audio(_silence(0.4))
        # Odd chunk sizes: frames must be reassembled across pushes.
        audio = _tone(0.6) + _silence(1.0)
        for i in range(0, len(audio), 999):
            await session.push_audio(audio[i:i + 999])
        await _settle(session)
        await session.close()
        return fakes, sink

    fakes, sink = asyncio.run(run())
    assert sink.of("input.speech_started") and sink.of("input.speech_stopped")
    assert [(e["role"], e["text"]) for e in sink.of("transcript")] == [
        ("user", "Hello agent"), ("assistant", "Hi! How can I help?")]
    assert sink.audio()


def test_caller_speech_interrupts_the_agent_and_clears_playback():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.replies = ["This is a long answer. It keeps going."]
        fakes.render_gate = asyncio.Event()  # the agent is stuck mid-sentence
        session = va.VoiceAgentSession(va.SessionConfig(), sink, fakes.deps())
        await session.start()
        await session.push_text("Tell me a story")
        for _ in range(20):
            await asyncio.sleep(0)
        assert session.state == "speaking"
        await session.push_audio(_silence(0.2) + _tone(0.5))
        state_after = session.state
        await session.close()
        return sink, state_after

    sink, state_after = asyncio.run(run())
    assert sink.of("audio.clear")[0]["reason"] == "barge_in"
    assert state_after == "listening"


def test_barge_in_can_be_switched_off():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.render_gate = asyncio.Event()
        session = va.VoiceAgentSession(va.SessionConfig(barge_in=False), sink, fakes.deps())
        await session.start()
        await session.push_text("Hi")
        for _ in range(20):
            await asyncio.sleep(0)
        await session.push_audio(_silence(0.2) + _tone(0.5))
        state = session.state
        await session.close()
        return sink, state

    sink, state = asyncio.run(run())
    assert not sink.of("audio.clear") and state == "speaking"


def test_a_failed_llm_turn_reports_an_error_and_keeps_listening():
    async def run():
        sink = Sink()
        fakes = Fakes()
        deps = fakes.deps()

        async def broken(messages):
            raise RuntimeError("no model")
            yield ""  # pragma: no cover

        deps.llm_stream = broken
        session = va.VoiceAgentSession(va.SessionConfig(), sink, deps)
        await session.start()
        await session.push_text("Hello?")
        await _settle(session)
        state = session.state
        await session.close()
        return sink, state

    sink, state = asyncio.run(run())
    assert sink.of("error")[0]["code"] == "turn_failed"
    assert state == "listening"


def test_greet_speaks_first():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.replies = ["Hello, I'm your assistant."]
        session = va.VoiceAgentSession(va.SessionConfig(greet=True), sink, fakes.deps())
        await session.start()
        await _settle(session)
        await session.close()
        return sink

    sink = asyncio.run(run())
    assert [e["role"] for e in sink.of("transcript")] == ["assistant"]


# ── WebSocket ──────────────────────────────────────────────────────────────


@pytest.fixture()
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import voice_agent as router

    fakes = Fakes()
    # Other suites pop and re-import service modules: patch the module object
    # the router actually holds, not this file's import.
    monkeypatch.setattr(router.voice_agent, "deps_factory", fakes.deps)
    monkeypatch.setattr(router, "is_local_host", lambda host: True)
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as c:
        c.fakes = fakes
        yield c


def _receive_until(ws, kind):
    seen = []
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            seen.append(message["bytes"])
            continue
        event = json.loads(message["text"])
        seen.append(event)
        if event.get("type") == kind:
            return seen


def test_websocket_text_conversation(client):
    client.fakes.replies = ["Four."]
    with client.websocket_connect("/v1/agent/ws") as ws:
        ws.send_text(json.dumps({"type": "session.start", "instructions": "Be brief."}))
        assert json.loads(ws.receive_text())["type"] == "session.ready"
        ws.send_text(json.dumps({"type": "input_text", "text": "Two plus two?"}))
        seen = _receive_until(ws, "response.done")
        ws.send_text(json.dumps({"type": "session.end"}))
    assert any(isinstance(e, bytes) for e in seen)
    texts = [e["text"] for e in seen if isinstance(e, dict) and e.get("type") == "transcript"]
    assert texts[0] == "Two plus two?" and "Four." in texts


def test_websocket_requires_session_start_first(client):
    with client.websocket_connect("/v1/agent/ws") as ws:
        ws.send_text(json.dumps({"type": "input_text", "text": "hi"}))
        event = json.loads(ws.receive_text())
    assert event["code"] == "bad_request"


def test_websocket_refuses_remote_clients_without_a_key(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from api.routers import voice_agent as router

    monkeypatch.setattr(router, "is_local_host", lambda host: False)
    monkeypatch.setattr(router, "ws_remote_authorized", lambda ws: False)
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/v1/agent/ws") as ws:
                ws.receive_text()
    assert exc.value.code == 1008


def test_browser_client_page_is_served(client):
    response = client.get("/agent")
    assert response.status_code == 200
    assert "/v1/agent/ws" in response.text and "<title>Voiceym Agent</title>" in response.text
