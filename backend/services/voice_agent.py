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
import logging
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
    #: (pcm16 at 16 kHz, language[, multilingual=True]) → text
    transcribe: Callable[[np.ndarray, str], Awaitable[str]]
    #: (text, voice=, engine=, language=) → async iterator of PCM16 at 24 kHz
    render: Callable[..., AsyncIterator[np.ndarray]]


async def _default_transcribe(pcm16k: np.ndarray, language: str, *, multilingual: bool = False) -> str:
    from api.routers.capture_ws import _transcribe_buffer, _transcribe_buffer_full
    from services.asr_backend import capture_lease

    audio = [pcm16k.astype(np.int16).tobytes()]
    with capture_lease():
        if multilingual:
            # Skip the dictation model (often English/European-only) for the
            # multilingual Whisper tier, which detects the spoken language.
            result = await _transcribe_buffer_full(audio, pcm_sr=INPUT_SAMPLE_RATE, skip_sherpa=True)
            return result.get("text", "")
        return await _transcribe_buffer(audio, pcm_sr=INPUT_SAMPLE_RATE)


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
    from services.telephony.agent import _default_llm_stream

    return VoiceAgentDeps(
        llm_stream=_default_llm_stream,
        transcribe=_default_transcribe,
        render=_default_render,
    )


#: Replaced by tests (fake LLM/ASR/TTS); production resolves real engines.
deps_factory: Callable[[], VoiceAgentDeps] = default_deps


# ── The session ────────────────────────────────────────────────────────────


#: Light engine for the built-in voice: small enough to load and speak in real
#: time on 8 GB machines, where the main cloning model can stall a live turn.
FAST_VOICE_ENGINE = "kittentts"

#: Kokoro-82M through MLX-Audio (Apple Silicon, Apache-2.0): a more natural
#: English voice that also speaks Hindi, preferred when its weights are on disk.
KOKORO_ENGINE = "mlx-audio"

#: Light multilingual engine for sentences neither voice above can say
#: (other Indian scripts and so on): quantized OmniVoice, 600+ languages.
MULTILINGUAL_VOICE_ENGINE = "omnivoice-gguf"

#: OmniVoice in all its forms is licensed CC-BY-NC: never chosen for a session
#: that asked for commercial-use voices only (the Talk screen always does).
NON_COMMERCIAL_ENGINES = frozenset({"omnivoice", "omnivoice-gguf", "omnivoice-subprocess"})


def active_tts_engine() -> str:
    try:
        from services.tts_backend import active_backend_id

        return active_backend_id()
    except Exception:  # noqa: BLE001
        return ""


def _engine_if_available(engine_id: str) -> str:
    try:
        from services.tts_backend import get_backend_class

        ok, _ = get_backend_class(engine_id).is_available()
    except Exception:  # noqa: BLE001 — an unknown or broken engine just falls back
        return ""
    return engine_id if ok else ""


def kokoro_ready() -> bool:
    """MLX-Audio runs here, is set to Kokoro, and Kokoro is already downloaded
    (never trigger a surprise download from a live conversation)."""
    if not _engine_if_available(KOKORO_ENGINE):
        return False
    try:
        from services.asr_backend import _repo_installed
        from services.tts_backend import MLXAudioBackend

        repo = MLXAudioBackend.CURATED_MODELS["kokoro"]
        return MLXAudioBackend.configured_identity() == repo and _repo_installed(repo)
    except Exception:  # noqa: BLE001
        return False


def fast_voice_engine() -> str:
    """The fast engine's id when it is installed, else "" (use the active engine)."""
    return KOKORO_ENGINE if kokoro_ready() else _engine_if_available(FAST_VOICE_ENGINE)


def multilingual_voice_engine() -> str:
    """The light multilingual engine's id when it is installed, else ""."""
    return _engine_if_available(MULTILINGUAL_VOICE_ENGINE)


def is_devanagari(sentence: str) -> bool:
    return any("\u0900" <= ch <= "\u097f" for ch in sentence)


_ENGLISH = {"", "en", "english"}


def needs_multilingual_voice(sentence: str, language: str) -> bool:
    """True when the English-only fast voice can't say this: a non-English
    session language, or letters outside the Latin script (Devanagari, Tamil…)."""
    if language.strip().lower() not in _ENGLISH:
        return True
    return any(ch.isalpha() and ord(ch) > 0x024F for ch in sentence)


@dataclass
class SessionConfig:
    instructions: str = DEFAULT_INSTRUCTIONS
    voice: str = ""
    engine: str = ""
    language: str = ""
    #: Speak first when the session starts (a greeting from the LLM).
    greet: bool = False
    #: Let caller speech interrupt the agent.
    barge_in: bool = True
    #: Hear with the multilingual Whisper tier instead of the dictation model.
    multilingual_asr: bool = False
    #: Engine for sentences the fast built-in voice can't speak ("" = none).
    multilingual_engine: str = ""
    #: Engine that speaks Hindi (Devanagari) for the built-in voice ("" = none).
    hindi_engine: str = ""
    #: Only commercial-use voices; never fall back to OmniVoice (CC-BY-NC).
    commercial_only: bool = False
    #: Why this session cannot speak at all ("" = it can): "voice" when no
    #: commercial-use engine is available, "clone" for a cloned voice without one.
    unavailable: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_message(cls, data: dict) -> "SessionConfig":
        def _s(key: str, limit: int = 200) -> str:
            value = data.get(key)
            return value.strip()[:limit] if isinstance(value, str) else ""

        voice, engine = _s("voice"), _s("engine")
        commercial_only = data.get("commercial_only") is True
        # A client may ask for the fast built-in voice when no cloned voice is
        # picked; a named voice or engine always wins.
        multilingual_engine = hindi_engine = unavailable = ""
        if not voice and not engine and data.get("fast_default_voice") is True:
            engine = fast_voice_engine()
            if engine:
                multilingual_engine = multilingual_voice_engine()
                hindi_engine = KOKORO_ENGINE if engine == KOKORO_ENGINE else ""
        if commercial_only:
            if multilingual_engine in NON_COMMERCIAL_ENGINES:
                multilingual_engine = ""
            if engine in NON_COMMERCIAL_ENGINES or (
                not engine and active_tts_engine() in NON_COMMERCIAL_ENGINES
            ):
                engine = ""
                unavailable = "clone" if voice else "voice"
        return cls(
            instructions=_s("instructions", MAX_INSTRUCTIONS_CHARS) or DEFAULT_INSTRUCTIONS,
            voice=voice,
            engine=engine,
            language=_s("language", 40),
            greet=bool(data.get("greet", False)),
            barge_in=bool(data.get("barge_in", True)),
            multilingual_asr=data.get("multilingual_asr") is True,
            multilingual_engine=multilingual_engine,
            hindi_engine=hindi_engine,
            commercial_only=commercial_only,
            unavailable=unavailable,
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
        self._warned: set[str] = set()

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
                if self.config.multilingual_asr:
                    heard = await self.deps.transcribe(pcm, self.config.language, multilingual=True)
                else:
                    heard = await self.deps.transcribe(pcm, self.config.language)
                text = (heard or "").strip()
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
                async for delta in self.deps.llm_stream(messages):
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

    _UNAVAILABLE = {
        "voice": "No commercial-use voice is installed, so replies are text only. "
                 "Install Kokoro or KittenTTS in Model Catalogue.",
        "clone": "Cloned voices need a commercial-use cloning engine, which isn't "
                 "installed. Pick the default voice, or install one in Model Catalogue.",
        "language": "The voice can't speak this language yet, so that part is text only.",
    }

    async def _voice_unavailable(self, reason: str) -> None:
        if reason in self._warned:
            return
        self._warned.add(reason)
        await self._emit({"type": "error", "code": f"voice_unavailable_{reason}",
                          "message": self._UNAVAILABLE[reason]})

    async def _speak(self, sentence: str) -> None:
        await self._set_state("speaking")
        engine, language = self.config.engine, self.config.language
        if self.config.hindi_engine and is_devanagari(sentence):
            engine, language = self.config.hindi_engine, "hi"
        elif self.config.multilingual_engine and needs_multilingual_voice(sentence, language):
            engine = self.config.multilingual_engine
        elif self.config.commercial_only and engine and needs_multilingual_voice(sentence, language):
            # The commercial-use voice can't say this script; stay silent and
            # say why rather than reaching for the non-commercial engine.
            await self._voice_unavailable("language")
            return
        if self.config.unavailable:
            await self._voice_unavailable(self.config.unavailable)
            return
        async for pcm in self.deps.render(
            sentence, voice=self.config.voice, engine=engine, language=language
        ):
            if pcm.size == 0:
                continue
            now = self._clock()
            self._playback_until = max(self._playback_until, now) + pcm.size / OUTPUT_SAMPLE_RATE
            await self._emit(pcm.astype("<i2").tobytes())
