"""The voice agent: a spoken conversation over any audio transport.

The phone agent (:mod:`services.telephony.agent`) only runs inside a Twilio
media stream. This is the same listen → think → speak loop with no telephony
attached, so a browser, a desktop mic, a script or a bot can talk to it:

    caller PCM16 16 kHz → :class:`~services.telephony.agent.Endpointer`
    (energy VAD) → utterance → ASR (the capture/dictation engine) → LLM turn
    (streamed; the first sentence starts synthesis) → streaming TTS in the
    chosen voice → PCM16 24 kHz back to the caller.

A session talks to its transport through one ``send`` callback that receives
either a JSON-able ``dict`` (state, transcripts, control) or ``bytes`` (audio).
Text input (:meth:`VoiceAgentSession.push_text`) works without any audio at all.

Barge-in: the transport plays audio after it has been sent, so the session
keeps a playback clock (the end of the audio it has sent, in wall time). Speech
from the caller while that clock is running cancels the turn and sends an
``audio.clear`` event so the client drops what it has buffered.

The LLM, ASR and TTS are injected through :class:`VoiceAgentDeps` so tests
(and other providers) can swap them without touching the loop.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Union

import numpy as np

from services.telephony.agent import Endpointer

logger = logging.getLogger("omnivoice.voice_agent")

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
FRAME_SAMPLES = INPUT_SAMPLE_RATE // 50  # 20 ms, the Endpointer's frame size
STATES = ("listening", "thinking", "speaking")
#: Conversation turns kept for the LLM (user + assistant messages).
MAX_HISTORY = 24
MAX_INSTRUCTIONS_CHARS = 4000
MAX_TEXT_INPUT_CHARS = 2000

DEFAULT_INSTRUCTIONS = (
    "You are a friendly, helpful voice assistant. Answer the user's questions and "
    "help with their requests."
)

Event = Union[dict, bytes]

#: Languages the light setup (Parakeet, the English voice) can't handle. Talk
#: hears them with multilingual Whisper and speaks them with OmniVoice.
INDIAN_LANGUAGES = {
    "hindi": "hi", "bengali": "bn", "tamil": "ta", "telugu": "te", "marathi": "mr",
    "gujarati": "gu", "kannada": "kn", "malayalam": "ml", "panjabi": "pa", "punjabi": "pa",
    "urdu": "ur", "odia": "or", "assamese": "as", "nepali": "ne",
}
#: Voice engines that speak Indian languages, lightest first: the GGUF build of
#: OmniVoice (about 0.7 GB) when its binary is installed, else OmniVoice itself.
MULTILINGUAL_ENGINES = ("omnivoice-gguf", "omnivoice")


def multilingual_engine() -> str:
    from services.tts_backend import get_backend_class

    for engine in MULTILINGUAL_ENGINES[:-1]:
        try:
            if get_backend_class(engine).is_available()[0]:
                return engine
        except Exception:  # noqa: BLE001 -- an unbuilt engine just isn't picked
            continue
    return MULTILINGUAL_ENGINES[-1]


def indian_language_code(language: str) -> str | None:
    """``"Hindi"`` or ``"hi"`` → ``"hi"``; ``None`` for any other language."""
    value = (language or "").strip().lower()
    if value in INDIAN_LANGUAGES.values():
        return value
    return INDIAN_LANGUAGES.get(value)


def is_indian_language(language: str) -> bool:
    return indian_language_code(language) is not None


def system_prompt(instructions: str, language: str) -> str:
    lang = (
        f"Speak {language}."
        if language and language.lower() not in ("auto", "")
        else "Reply in the language the user speaks."
    )
    return (
        f"{(instructions or DEFAULT_INSTRUCTIONS).strip()}\n\n"
        "You are talking out loud with the user; everything you write is spoken.\n"
        "- Keep replies short and natural for speech: one to three sentences unless asked for more.\n"
        "- No markdown, lists, code blocks, emoji or URLs.\n"
        "- If you are asked whether you are a person or an AI, say truthfully that you are an AI assistant.\n"
        f"- {lang}"
    )


class ThinkFilter:
    """Drop a leading ``<think>…</think>`` block from streamed LLM text, so a
    reasoning model's private reasoning is never spoken."""

    _OPEN = re.compile(r"^\s*<(think|thinking|reasoning)>", re.IGNORECASE)
    _TAGS = ("<think>", "<thinking>", "<reasoning>")

    def __init__(self) -> None:
        self._buf = ""
        self._close: re.Pattern | None = None
        self._decided = False

    def feed(self, delta: str) -> str:
        if self._decided and self._close is None:
            return delta
        self._buf += delta or ""
        if not self._decided:
            stripped = self._buf.lstrip()
            if not stripped or any(tag.startswith(stripped.lower()) for tag in self._TAGS):
                return ""  # could still become an opening tag
            match = self._OPEN.match(self._buf)
            self._decided = True
            if not match:
                out, self._buf = self._buf, ""
                return out
            self._close = re.compile(rf"</{match.group(1)}>", re.IGNORECASE)
        end = self._close.search(self._buf)
        if not end:
            return ""
        out, self._buf, self._close = self._buf[end.end():].lstrip(), "", None
        return out

    def finish(self) -> str:
        if self._close is not None or self._decided:
            return ""  # an unclosed reasoning block is never spoken
        out, self._buf = self._buf, ""
        return out


# ── Dependencies ───────────────────────────────────────────────────────────


@dataclass
class VoiceAgentDeps:
    #: messages → async iterator of text deltas
    llm_stream: Callable[[list[dict]], AsyncIterator[str]]
    #: (pcm16 at 16 kHz, language) → text
    transcribe: Callable[[np.ndarray, str], Awaitable[str]]
    #: (text, voice=, engine=, language=) → async iterator of PCM16 at 24 kHz
    render: Callable[..., AsyncIterator[np.ndarray]]
    #: (messages, model name) → text deltas, when the session names an Ollama model
    model_stream: Callable[[list[dict], str], AsyncIterator[str]] | None = None


async def _default_transcribe(pcm16k: np.ndarray, language: str) -> str:
    from api.routers.capture_ws import _transcribe_buffer
    from services.asr_backend import capture_lease

    with capture_lease():
        if is_indian_language(language):
            return await _transcribe_multilingual(pcm16k, indian_language_code(language) or "")
        return await _transcribe_buffer([pcm16k.astype(np.int16).tobytes()], pcm_sr=INPUT_SAMPLE_RATE)


_multilingual_asr_backend = None


def _multilingual_asr():
    """A Whisper backend that takes a ``language`` (the dictation pick may be
    an English/European-only model such as Parakeet)."""
    global _multilingual_asr_backend
    from services import asr_backend as asr

    backend = asr.get_capture_asr_backend(skip_sherpa=True)
    if "language" in inspect.signature(backend.transcribe).parameters:
        return backend
    if _multilingual_asr_backend is None:
        ok, _ = asr.MLXWhisperBackend.is_available()
        _multilingual_asr_backend = (
            asr.MLXWhisperBackend(model_name=asr._MLX_MODEL_TURBO) if ok else asr.FasterWhisperBackend()
        )
    asr.ensure_backend_licence(_multilingual_asr_backend)
    return _multilingual_asr_backend


async def _transcribe_multilingual(pcm16k: np.ndarray, code: str) -> str:
    from api.routers.capture_ws import _pcm16_to_wav, _result_text
    from services.asr_backend import run_transcribe_guarded
    from services.model_manager import _gpu_pool

    path = _pcm16_to_wav(pcm16k.astype(np.int16).tobytes(), INPUT_SAMPLE_RATE)
    if path is None:
        return ""

    def _run() -> str:
        result = _multilingual_asr().transcribe(path, word_timestamps=False, language=code or None)
        return _result_text(result)

    try:
        return (await run_transcribe_guarded(_gpu_pool, _run, what="Voice agent")).strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def to_pcm16_24k(samples, sample_rate: int) -> np.ndarray:
    """Any-rate float audio (tensor or array) → int16 mono at 24 kHz."""
    from services.telephony.audio import float_to_pcm16

    if hasattr(samples, "detach"):
        samples = samples.detach().cpu().float().numpy()
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[0]
    arr = arr.reshape(-1)
    if sample_rate != OUTPUT_SAMPLE_RATE and arr.size:
        import torch
        import torchaudio

        arr = torchaudio.functional.resample(
            torch.from_numpy(np.ascontiguousarray(arr)), int(sample_rate), OUTPUT_SAMPLE_RATE
        ).numpy()
    return float_to_pcm16(arr)


async def _default_render(
    text: str, *, voice: str = "", engine: str = "", language: str = ""
) -> AsyncIterator[np.ndarray]:
    from api.routers.tts_stream import synthesize_stream

    async for wav, sr in synthesize_stream(
        text, voice=voice or None, engine=engine or None, language=language or None
    ):
        yield await asyncio.to_thread(to_pcm16_24k, wav, sr)


def default_deps() -> VoiceAgentDeps:
    from services import ollama_local
    from services.telephony.agent import _default_llm_stream

    return VoiceAgentDeps(
        llm_stream=_default_llm_stream,
        transcribe=_default_transcribe,
        render=_default_render,
        model_stream=ollama_local.chat_stream,
    )


#: Replaced by tests (fake LLM/ASR/TTS); production resolves real engines.
deps_factory: Callable[[], VoiceAgentDeps] = default_deps


# ── The session ────────────────────────────────────────────────────────────


@dataclass
class SessionConfig:
    instructions: str = DEFAULT_INSTRUCTIONS
    voice: str = ""
    engine: str = ""
    language: str = ""
    #: An Ollama model to answer with; empty uses the app's language model.
    model: str = ""
    #: Speak first when the session starts (a greeting from the LLM).
    greet: bool = False
    #: Let caller speech interrupt the agent.
    barge_in: bool = True
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_message(cls, data: dict) -> "SessionConfig":
        def _s(key: str, limit: int = 200) -> str:
            value = data.get(key)
            return value.strip()[:limit] if isinstance(value, str) else ""

        return cls(
            instructions=_s("instructions", MAX_INSTRUCTIONS_CHARS) or DEFAULT_INSTRUCTIONS,
            voice=_s("voice"),
            engine=_s("engine"),
            language=_s("language", 40),
            model=_s("model", 120),
            greet=bool(data.get("greet", False)),
            barge_in=bool(data.get("barge_in", True)),
        )


class VoiceAgentSession:
    """One conversation. All methods run on the transport's event loop."""

    def __init__(
        self,
        config: SessionConfig,
        send: Callable[[Event], Awaitable[None]],
        deps: VoiceAgentDeps | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.deps = deps or deps_factory()
        self.state = "listening"
        self._send = send
        self._clock = clock
        self._vad = Endpointer()
        self._history: list[dict] = []
        self._turn: asyncio.Task | None = None
        self._asr_q: asyncio.Queue = asyncio.Queue()
        self._asr_task: asyncio.Task | None = None
        self._frame_rest = np.zeros(0, dtype=np.int16)
        self._playback_until = 0.0
        self._closed = False

    # ── Transport surface ──

    async def start(self) -> None:
        self._asr_task = asyncio.create_task(self._asr_worker())
        await self._emit({"type": "session.ready", "input_sample_rate": INPUT_SAMPLE_RATE,
                          "output_sample_rate": OUTPUT_SAMPLE_RATE})
        await self._set_state("listening")
        if self.config.greet:
            self._history.append({"role": "user", "content": "[The user just connected. Greet them briefly.]"})
            self._start_turn()

    async def push_audio(self, data: bytes) -> None:
        """Caller audio: raw little-endian PCM16 mono at 16 kHz, any chunking."""
        if self._closed or not data:
            return
        usable = len(data) - len(data) % 2
        pcm = np.concatenate([self._frame_rest, np.frombuffer(data[:usable], dtype="<i2").astype(np.int16)])
        whole = pcm.size - pcm.size % FRAME_SAMPLES
        self._frame_rest = pcm[whole:]
        for i in range(0, whole, FRAME_SAMPLES):
            for event in self._vad.push(pcm[i:i + FRAME_SAMPLES]):
                if event == "barge":
                    await self.interrupt(reason="barge_in")
                elif event == "start":
                    await self._emit({"type": "input.speech_started"})
                elif isinstance(event, tuple):
                    await self._emit({"type": "input.speech_stopped"})
                    self._asr_q.put_nowait(event[1])

    async def push_text(self, text: str) -> None:
        """A typed user message: answered exactly like a spoken one."""
        text = (text or "").strip()[:MAX_TEXT_INPUT_CHARS]
        if text and not self._closed:
            await self.interrupt(reason="new_input")
            await self._on_user_text(text)

    async def interrupt(self, *, reason: str = "client") -> None:
        """Stop the current reply and tell the client to drop buffered audio."""
        if reason == "barge_in" and (not self.config.barge_in or not self._is_speaking()):
            return
        turn = self._turn
        busy = turn is not None and not turn.done()
        if busy:
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        if busy or self._is_speaking():
            self._playback_until = 0.0
            await self._emit({"type": "audio.clear", "reason": reason})
            await self._set_state("listening")

    async def close(self) -> None:
        self._closed = True
        for task in (self._turn, self._asr_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (self._turn, self._asr_task) if t is not None), return_exceptions=True)

    # ── Internals ──

    def _is_speaking(self) -> bool:
        return self.state == "speaking" or self._clock() < self._playback_until

    async def _emit(self, event: Event) -> None:
        if not self._closed:
            await self._send(event)

    async def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            await self._emit({"type": "state", "state": state})

    def _messages(self) -> list[dict]:
        prompt = system_prompt(self.config.instructions, self.config.language)
        return [{"role": "system", "content": prompt}, *self._history[-MAX_HISTORY:]]

    async def _asr_worker(self) -> None:
        while True:
            pcm = await self._asr_q.get()
            try:
                text = (await self.deps.transcribe(pcm, self.config.language) or "").strip()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — skip the utterance, keep listening
                logger.warning("Voice agent transcription failed (%s)", type(exc).__name__)
                await self._emit({"type": "error", "code": "transcription_failed",
                                  "message": "Could not transcribe that; please try again."})
                continue
            if text and not self._closed:
                await self._on_user_text(text)

    async def _on_user_text(self, text: str) -> None:
        await self._emit({"type": "transcript", "role": "user", "text": text})
        self._history.append({"role": "user", "content": text})
        self._start_turn()

    def _start_turn(self) -> None:
        previous = self._turn
        if previous is not None and not previous.done():
            previous.cancel()
        self._turn = asyncio.get_running_loop().create_task(self._run_turn(previous))

    async def _run_turn(self, previous: asyncio.Task | None) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        spoken: list[str] = []
        try:
            await self._llm_turn(spoken)
        except asyncio.CancelledError:
            if spoken:
                self._record_interrupted(" ".join(spoken))
            raise
        except Exception as exc:  # noqa: BLE001 — a failed turn must not end the session
            logger.warning("Voice agent turn failed (%s)", type(exc).__name__)
            if spoken:
                await self._emit(self._record(" ".join(spoken), interrupted=True))
            await self._emit({"type": "error", "code": "turn_failed",
                              "message": "The assistant could not answer. Check the language model and voice settings."})
            await self._set_state("listening")

    def _record(self, text: str, *, interrupted: bool = False) -> dict:
        """Add the reply to the history; returns its transcript event."""
        self._history.append({"role": "assistant", "content": text})
        event = {"type": "transcript", "role": "assistant", "text": text}
        if interrupted:
            event["interrupted"] = True
        return event

    def _record_interrupted(self, text: str) -> None:
        event = self._record(text, interrupted=True)
        if not self._closed:
            # Runs while the turn task is being cancelled, so it cannot await.
            asyncio.get_running_loop().create_task(self._emit(event))

    async def _llm_turn(self, spoken: list[str]) -> None:
        from services.sentence_chunker import SentenceChunker

        await self._set_state("thinking")
        think = ThinkFilter()
        chunker = SentenceChunker(language=(self.config.language or "en"), aggressive_first_flush=True)
        sentences: asyncio.Queue = asyncio.Queue()
        messages = self._messages()

        async def _produce() -> None:
            try:
                if self.config.model and self.deps.model_stream is not None:
                    stream = self.deps.model_stream(messages, self.config.model)
                else:
                    stream = self.deps.llm_stream(messages)
                async for delta in stream:
                    for sentence in chunker.push(think.feed(delta)):
                        await sentences.put(sentence)
                for sentence in chunker.push(think.finish()) + chunker.flush():
                    await sentences.put(sentence)
            finally:
                await sentences.put(None)

        producer = asyncio.create_task(_produce())
        try:
            while True:
                sentence = await sentences.get()
                if sentence is None:
                    break
                sentence = sentence.strip()
                if not sentence:
                    continue
                await self._speak(sentence)
                spoken.append(sentence)
                await self._emit({"type": "transcript.delta", "role": "assistant", "text": sentence})
            await asyncio.gather(producer)  # re-raises an LLM failure
        finally:
            if not producer.done():
                producer.cancel()
        if spoken:
            await self._emit(self._record(" ".join(spoken)))
            await self._emit({"type": "response.done"})
        await self._set_state("listening")

    def _engine(self) -> str:
        """The voice engine for this session: the client's pick, else OmniVoice
        for an Indian language (the light voice is English only)."""
        if self.config.engine:
            return self.config.engine
        return multilingual_engine() if is_indian_language(self.config.language) else ""

    async def _speak(self, sentence: str) -> None:
        await self._set_state("speaking")
        async for pcm in self.deps.render(
            sentence, voice=self.config.voice, engine=self._engine(), language=self.config.language
        ):
            if pcm.size == 0:
                continue
            now = self._clock()
            self._playback_until = max(self._playback_until, now) + pcm.size / OUTPUT_SAMPLE_RATE
            await self._emit(pcm.astype("<i2").tobytes())
