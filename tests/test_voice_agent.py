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
        self.engines: list[str] = []
        self.languages: list[str] = []
        self.multilingual_calls: list[bool] = []
        self.llm_calls: list[list[dict]] = []
        self.render_gate: asyncio.Event | None = None

    def deps(self):
        async def llm_stream(messages):
            self.llm_calls.append(messages)
            reply = self.replies.pop(0) if self.replies else "Okay."
            for i in range(0, len(reply), 5):
                yield reply[i:i + 5]
                await asyncio.sleep(0)

        async def transcribe(pcm, language, multilingual=False):
            self.multilingual_calls.append(multilingual)
            assert pcm.dtype == np.int16 and pcm.size > 0
            return self.heard.pop(0) if self.heard else ""

        async def render(text, **kw):
            self.rendered.append(text)
            self.engines.append(kw.get("engine", ""))
            self.languages.append(kw.get("language", ""))
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


def test_fast_default_voice_only_applies_without_a_chosen_voice(monkeypatch):
    monkeypatch.setattr(va, "fast_voice_engine", lambda: "kittentts")
    pick = va.SessionConfig.from_message
    assert pick({"fast_default_voice": True}).engine == "kittentts"
    assert pick({"fast_default_voice": True, "voice": "p1"}).engine == ""
    assert pick({"fast_default_voice": True, "engine": "omnivoice"}).engine == "omnivoice"
    assert pick({}).engine == ""
    monkeypatch.setattr(va, "fast_voice_engine", lambda: "")
    assert pick({"fast_default_voice": True}).engine == ""


def test_fast_voice_pairs_with_the_multilingual_engine(monkeypatch):
    monkeypatch.setattr(va, "fast_voice_engine", lambda: "kittentts")
    monkeypatch.setattr(va, "multilingual_voice_engine", lambda: "omnivoice-gguf")
    pick = va.SessionConfig.from_message
    assert pick({"fast_default_voice": True}).multilingual_engine == "omnivoice-gguf"
    assert pick({"fast_default_voice": True, "voice": "p1"}).multilingual_engine == ""
    assert pick({"multilingual_asr": True}).multilingual_asr is True
    assert pick({"multilingual_asr": "yes"}).multilingual_asr is False


def test_kokoro_is_preferred_and_also_speaks_hindi(monkeypatch):
    monkeypatch.setattr(va, "kokoro_ready", lambda: True)
    monkeypatch.setattr(va, "multilingual_voice_engine", lambda: "omnivoice-gguf")
    cfg = va.SessionConfig.from_message({"fast_default_voice": True})
    assert (cfg.engine, cfg.hindi_engine, cfg.multilingual_engine) == (
        "kokoro", "kokoro", "omnivoice-gguf")
    monkeypatch.setattr(va, "kokoro_ready", lambda: False)
    monkeypatch.setattr(va, "_engine_if_available", lambda engine: engine)
    cfg = va.SessionConfig.from_message({"fast_default_voice": True})
    assert (cfg.engine, cfg.hindi_engine) == ("kittentts", "")


def test_hindi_goes_to_kokoro_other_scripts_to_the_multilingual_engine():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.replies = ["\u092d\u093e\u0930\u0924 \u0915\u0940 \u0930\u093e\u091c\u0927\u093e\u0928\u0940 \u0928\u0908 \u0926\u093f\u0932\u094d\u0932\u0940 \u0939\u0948\u0964 "
                         "\u0c2d\u0c3e\u0c30\u0c24\u0c26\u0c47\u0c36 \u0c30\u0c3e\u0c1c\u0c27\u0c3e\u0c28\u0c3f \u0c28\u0c41\u0c2f\u0c4d \u0c22\u0c3f\u0c32\u0c4d\u0c32\u0c40, \u0c05\u0c26\u0c3f \u0c1a\u0c3e\u0c32\u0c3e \u0c2a\u0c46\u0c26\u0c4d\u0c26 \u0c28\u0c17\u0c30\u0c02."]
        config = va.SessionConfig(engine="kokoro", hindi_engine="kokoro",
                                  multilingual_engine="omnivoice-gguf")
        session = va.VoiceAgentSession(config, sink, fakes.deps())
        await session.start()
        await session.push_text("Capital?")
        await _settle(session)
        await session.close()
        return fakes

    fakes = asyncio.run(run())
    assert len(fakes.rendered) == 2, fakes.rendered
    assert fakes.engines == ["kokoro", "omnivoice-gguf"]
    assert fakes.languages == ["hi", ""]


def test_commercial_only_never_picks_omnivoice(monkeypatch):
    monkeypatch.setattr(va, "fast_voice_engine", lambda: "kittentts")
    monkeypatch.setattr(va, "multilingual_voice_engine", lambda: "omnivoice-gguf")
    monkeypatch.setattr(va, "active_tts_engine", lambda: "omnivoice")
    pick = va.SessionConfig.from_message
    talk = pick({"fast_default_voice": True, "commercial_only": True})
    assert (talk.engine, talk.multilingual_engine, talk.unavailable) == ("kittentts", "", "")
    monkeypatch.setattr(va, "cloning_voice_engine", lambda: "")
    cloned = pick({"voice": "p1", "fast_default_voice": True, "commercial_only": True})
    assert (cloned.engine, cloned.unavailable) == ("", "clone")
    monkeypatch.setattr(va, "cloning_voice_engine", lambda: "chatterbox")
    cloned = pick({"voice": "p1", "fast_default_voice": True, "commercial_only": True})
    assert (cloned.engine, cloned.unavailable) == ("chatterbox", "")
    monkeypatch.setattr(va, "fast_voice_engine", lambda: "")
    none = pick({"fast_default_voice": True, "commercial_only": True})
    assert (none.engine, none.unavailable) == ("", "voice")
    # An API client that names OmniVoice explicitly without the flag still gets it.
    assert pick({"engine": "omnivoice"}).engine == "omnivoice"


def test_commercial_only_says_why_instead_of_speaking_other_scripts():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.replies = ["Hello there, it is lovely to meet you today. "
                         "\u0c2d\u0c3e\u0c30\u0c24\u0c26\u0c47\u0c36 \u0c30\u0c3e\u0c1c\u0c27\u0c3e\u0c28\u0c3f \u0c28\u0c41\u0c2f\u0c4d \u0c22\u0c3f\u0c32\u0c4d\u0c32\u0c40, \u0c05\u0c26\u0c3f \u0c1a\u0c3e\u0c32\u0c3e \u0c2a\u0c46\u0c26\u0c4d\u0c26 \u0c28\u0c17\u0c30\u0c02."]
        config = va.SessionConfig(engine="kittentts", commercial_only=True)
        session = va.VoiceAgentSession(config, sink, fakes.deps())
        await session.start()
        await session.push_text("Hi")
        await _settle(session)
        await session.close()
        return fakes, sink

    fakes, sink = asyncio.run(run())
    assert fakes.engines == ["kittentts"]
    assert [e["code"] for e in sink.of("error")] == ["voice_unavailable_language"]
    assert len(sink.of("transcript.delta")) == 2


def test_commercial_only_keeps_indic_parler_for_indian_languages(monkeypatch):
    monkeypatch.setattr(va, "kokoro_ready", lambda: True)
    monkeypatch.setattr(va, "_engine_if_available", lambda engine: engine)
    talk = va.SessionConfig.from_message({"fast_default_voice": True, "commercial_only": True})
    assert (talk.engine, talk.hindi_engine, talk.multilingual_engine) == (
        "kokoro", "kokoro", "indic-parler")
    # Without it, the light OmniVoice build is offered, and Talk drops it.
    monkeypatch.setattr(va, "_engine_if_available", lambda e: "" if e == "indic-parler" else e)
    assert va.multilingual_voice_engine() == "omnivoice-gguf"
    talk = va.SessionConfig.from_message({"fast_default_voice": True, "commercial_only": True})
    assert talk.multilingual_engine == ""


def test_telugu_and_marathi_go_to_indic_parler_hindi_stays_with_kokoro():
    hindi = "\u092d\u093e\u0930\u0924 \u0915\u0940 \u0930\u093e\u091c\u0927\u093e\u0928\u0940 \u0928\u0908 \u0926\u093f\u0932\u094d\u0932\u0940 \u0939\u0948\u0964"
    telugu = "\u0c2d\u0c3e\u0c30\u0c24\u0c26\u0c47\u0c36 \u0c30\u0c3e\u0c1c\u0c27\u0c3e\u0c28\u0c3f \u0c28\u0c41\u0c2f\u0c4d \u0c22\u0c3f\u0c32\u0c4d\u0c32\u0c40."

    async def run(language):
        fakes, sink = Fakes(), Sink()
        fakes.replies = [f"{hindi} {telugu}"]
        config = va.SessionConfig(engine="kokoro", hindi_engine="kokoro", language=language,
                                  multilingual_engine="indic-parler", commercial_only=True)
        session = va.VoiceAgentSession(config, sink, fakes.deps())
        await session.start()
        await session.push_text("Capital?")
        await _settle(session)
        await session.close()
        return fakes, sink

    fakes, sink = asyncio.run(run(""))
    assert fakes.engines == ["kokoro", "indic-parler"]
    assert sink.of("error") == []
    # Marathi is written in Devanagari too: it must not be read as Hindi.
    fakes, _ = asyncio.run(run("Marathi"))
    assert fakes.engines == ["indic-parler", "indic-parler"]


def test_a_session_without_a_commercial_voice_answers_in_text():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.replies = ["Sure thing. The answer is four."]
        config = va.SessionConfig(commercial_only=True, unavailable="voice")
        session = va.VoiceAgentSession(config, sink, fakes.deps())
        await session.start()
        await session.push_text("Two plus two?")
        await _settle(session)
        await session.close()
        return fakes, sink

    fakes, sink = asyncio.run(run())
    assert fakes.rendered == []
    assert [e["code"] for e in sink.of("error")] == ["voice_unavailable_voice"]
    assert sink.of("response.done")


def test_needs_multilingual_voice_by_script_or_language():
    assert not va.needs_multilingual_voice("The capital is Paris.", "")
    assert not va.needs_multilingual_voice("Café crème, s'il vous plaît.", "English")
    assert va.needs_multilingual_voice("\u092d\u093e\u0930\u0924 \u0915\u0940 \u0930\u093e\u091c\u0927\u093e\u0928\u0940", "")
    assert va.needs_multilingual_voice("Namaste, aap kaise hain?", "Hindi")


def test_each_sentence_uses_the_voice_that_can_say_it():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.replies = ["Hello there, it is lovely to meet you today. "
                         "\u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u094b\u0938\u094d\u0924, \u0906\u092a \u0915\u0948\u0938\u0947 \u0939\u0948\u0902?"]
        config = va.SessionConfig(engine="kittentts", multilingual_engine="omnivoice-gguf")
        session = va.VoiceAgentSession(config, sink, fakes.deps())
        await session.start()
        await session.push_text("Say hi in two languages")
        await _settle(session)
        await session.close()
        return fakes

    fakes = asyncio.run(run())
    assert len(fakes.rendered) == 2, fakes.rendered
    assert fakes.engines == ["kittentts", "omnivoice-gguf"]


def test_multilingual_hearing_asks_for_the_multilingual_recogniser():
    async def run():
        fakes, sink = Fakes(), Sink()
        fakes.heard = ["namaste"]
        session = va.VoiceAgentSession(va.SessionConfig(multilingual_asr=True), sink, fakes.deps())
        await session.start()
        audio = _tone(0.6) + _silence(1.0)
        for i in range(0, len(audio), 999):
            await session.push_audio(audio[i:i + 999])
        await _settle(session)
        await session.close()
        return fakes

    assert asyncio.run(run()).multilingual_calls == [True]


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


def test_client_that_vanished_mid_session_does_not_crash_the_handler(monkeypatch):
    """Closing a socket the client already dropped raises WebSocketDisconnect
    in starlette; the handler must swallow it, not surface an ASGI error."""
    from types import SimpleNamespace

    from starlette.websockets import WebSocketDisconnect

    from api.routers import voice_agent as router

    monkeypatch.setattr(router.voice_agent, "deps_factory", Fakes().deps)
    monkeypatch.setattr(router, "is_local_host", lambda host: True)
    inbox = [
        {"type": "websocket.receive", "text": json.dumps({"type": "session.start"})},
        {"type": "websocket.disconnect", "code": 1006},
    ]

    class GoneSocket:
        headers: dict = {}
        client = SimpleNamespace(host="127.0.0.1")

        async def accept(self):
            pass

        async def receive(self):
            return inbox.pop(0)

        async def send_text(self, _text):
            pass

        async def send_bytes(self, _data):
            pass

        async def close(self, code=1000, reason=None):
            raise WebSocketDisconnect(code=1006)

    asyncio.run(router.agent_ws(GoneSocket()))


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


def test_a_named_ollama_model_answers_instead_of_the_app_model():
    async def run():
        fakes, sink = Fakes(), Sink()
        deps = fakes.deps()
        asked: list[str] = []

        async def model_stream(messages, model):
            asked.append(model)
            yield "From the small model."

        deps.model_stream = model_stream
        config = va.SessionConfig.from_message({"model": "llama3.2:1b"})
        session = va.VoiceAgentSession(config, sink, deps)
        await session.start()
        await session.push_text("Hello?")
        await _settle(session)
        await session.close()
        return fakes, sink, asked

    fakes, sink, asked = asyncio.run(run())
    assert asked == ["llama3.2:1b"] and fakes.llm_calls == []
    assert fakes.rendered == ["From the small model."]


def test_ollama_default_prefers_the_lightest_preferred_model():
    from services import ollama_local

    assert ollama_local.pick_default(["llama3.2:3b", "llama3.2:1b"]) == "llama3.2:1b"
    assert ollama_local.pick_default(["mistral:latest"]) == "mistral:latest"
    assert ollama_local.pick_default([]) == ""
    assert ollama_local.pick_default(
        ["llama3.2:1b", "gemma3:4b"], ollama_local.MULTILINGUAL_MODELS) == "gemma3:4b"


def test_indian_languages_are_spoken_with_the_multilingual_voice():
    assert va.is_indian_language("Hindi") and va.is_indian_language("ta")
    assert not va.is_indian_language("English") and not va.is_indian_language("")

    async def run(language, engine=""):
        fakes, sink = Fakes(), Sink()
        engines: list[str] = []
        deps = fakes.deps()

        async def render(text, **kw):
            engines.append(kw.get("engine"))
            yield np.zeros(2400, dtype=np.int16)

        deps.render = render
        config = va.SessionConfig.from_message({"language": language, "engine": engine})
        session = va.VoiceAgentSession(config, sink, deps)
        await session.start()
        await session.push_text("Hello?")
        await _settle(session)
        await session.close()
        return engines

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(va, "multilingual_engine", lambda: "omnivoice-gguf")
        assert asyncio.run(run("Hindi")) == ["omnivoice-gguf"]
    assert asyncio.run(run("English")) == [""]
    assert asyncio.run(run("Tamil", engine="kittentts")) == ["kittentts"]


def test_models_endpoint_reports_ollama_unavailable(client, monkeypatch):
    import httpx

    from services import ollama_local

    class Down:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(ollama_local.httpx, "AsyncClient", Down)
    response = client.get("/v1/agent/models")
    assert response.status_code == 200
    assert response.json() == {
        "available": False, "models": [], "default": "", "multilingual_default": ""}


def test_ollama_chat_stream_sends_a_short_context_and_yields_text(monkeypatch):
    import httpx

    from services import ollama_local

    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        lines = [
            {"message": {"content": "Hi "}, "done": False},
            {"message": {"content": "there."}, "done": False},
            {"done": True},
        ]
        return httpx.Response(200, text="\n".join(json.dumps(line) for line in lines))

    real = httpx.AsyncClient
    monkeypatch.setattr(
        ollama_local.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )

    async def run():
        return [d async for d in ollama_local.chat_stream([{"role": "user", "content": "hey"}], "llama3.2:1b")]

    assert asyncio.run(run()) == ["Hi ", "there."]
    assert sent["model"] == "llama3.2:1b" and sent["options"]["num_ctx"] == ollama_local.TALK_NUM_CTX
