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
#: hears them with multilingual Whisper and speaks them with Indic Parler-TTS
#: (or, outside commercial-only sessions, OmniVoice).
INDIAN_LANGUAGES = {
    "hindi": "hi", "bengali": "bn", "tamil": "ta", "telugu": "te", "marathi": "mr",
    "gujarati": "gu", "kannada": "kn", "malayalam": "ml", "panjabi": "pa", "punjabi": "pa",
    "urdu": "ur", "odia": "or", "assamese": "as", "nepali": "ne",
}
#: Voice engines that speak Indian languages, in order of preference: Indic
#: Parler-TTS (Apache-2.0) or Indic-TTS (MIT) when installed, the GGUF build
#: of OmniVoice (about 0.7 GB) when its binary is installed, else OmniVoice.
MULTILINGUAL_ENGINES = ("indic-parler", "indic-tts", "omnivoice-gguf", "omnivoice")


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
        "- Answer exactly what the user asked. If you didn't understand, say so briefly and ask them to repeat.\n"
        "- No markdown, lists, code blocks, emoji or URLs.\n"
        "- If you are asked whether you are a person or an AI, say truthfully that you are an AI assistant.\n"
        f"- {lang}"
    )


_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3]+"
)


def strip_emoji(text: str) -> str:
    """Small models add emoji despite the prompt; they are never spoken or shown."""
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text))


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
    #: (messages, model name) → text deltas, when the session names an Ollama model
    model_stream: Callable[[list[dict], str], AsyncIterator[str]] | None = None


async def _default_transcribe(pcm16k: np.ndarray, language: str, *, multilingual: bool = False) -> str:
    from api.routers.capture_ws import _transcribe_buffer, _transcribe_buffer_full
    from services.asr_backend import capture_lease

    audio = [pcm16k.astype(np.int16).tobytes()]
    with capture_lease():
        if is_indian_language(language):
            # A named Indian language: Whisper with that language as a hint.
            return await _transcribe_multilingual(pcm16k, indian_language_code(language) or "")
        if multilingual:
            # Auto or another non-English language: skip the dictation model
            # (often English/European-only) for the multilingual Whisper tier,
            # which detects the spoken language.
            result = await _transcribe_buffer_full(audio, pcm_sr=INPUT_SAMPLE_RATE, skip_sherpa=True)
            return result.get("text", "")
        return await _transcribe_buffer(audio, pcm_sr=INPUT_SAMPLE_RATE)


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
        from services import indic_asr

        # A Whisper fine-tuned for this language (Telugu, Hindi) hears it far
        # better than the general multilingual model, when it is installed.
        if code and indic_asr.installed(code):
            return indic_asr.transcribe(path, code)
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


#: Light engine for the built-in voice: small enough to load and speak in real
#: time on 8 GB machines, where the main cloning model can stall a live turn.
FAST_VOICE_ENGINE = "kittentts"

#: Kokoro-82M on ONNX Runtime (Apache-2.0, every platform): a natural English
#: voice that also speaks Hindi, preferred when its weights are on disk.
KOKORO_ENGINE = "kokoro"


#: Commercial-use (Apache-2.0) engine for the Indian languages the voices
#: above can't say (Tamil, Telugu, Bengali and the rest), when installed.
INDIC_VOICE_ENGINE = "indic-parler"

#: The same with no account (AI4Bharat Indic-TTS, MIT): each language's voice
#: downloads from GitHub the first time a reply needs it.
INDIC_TTS_ENGINE = "indic-tts"

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
    """The Kokoro engine runs here and its model is already downloaded
    (never trigger a surprise download from a live conversation)."""
    if not _engine_if_available(KOKORO_ENGINE):
        return False
    try:
        from engines.kokoro import GRAPH_FILE, REPO_ID
        from huggingface_hub import try_to_load_from_cache
        from services.hf_revisions import revision_for

        cached = try_to_load_from_cache(REPO_ID, GRAPH_FILE, revision=revision_for(REPO_ID))
        return isinstance(cached, str)
    except Exception:  # noqa: BLE001
        return False


def fast_voice_engine() -> str:
    """The fast engine's id when it is installed, else "" (use the active engine)."""
    return KOKORO_ENGINE if kokoro_ready() else _engine_if_available(FAST_VOICE_ENGINE)


#: Commercial-use (MIT) English voice cloning, when installed.
CLONING_VOICE_ENGINE = "chatterbox"


def cloning_voice_engine() -> str:
    """The commercial-use cloning engine's id when it is installed, else ""."""
    return _engine_if_available(CLONING_VOICE_ENGINE)


def multilingual_voice_engine() -> str:
    """The engine for scripts the fast voice can't say: Indic Parler-TTS or
    Indic-TTS when installed, else the light multilingual engine when
    installed, else ""."""
    return (
        _engine_if_available(INDIC_VOICE_ENGINE)
        or _engine_if_available(INDIC_TTS_ENGINE)
        or _engine_if_available(MULTILINGUAL_VOICE_ENGINE)
    )


def indic_tts_voice(sentence: str, language: str) -> tuple[str, str]:
    """Indic-TTS's language for *sentence* and whether its voice is ready:
    ``(code, "ready" | "downloading" | "failed")``, or ``("", "")`` when
    Indic-TTS can't say it. A missing voice starts downloading in the
    background, so a live conversation never waits on it."""
    from engines.indic_tts import voices
    from engines.indic_tts.indic_tts_languages import detect_language

    code = detect_language(sentence, language)
    if code is None:
        return "", ""
    if voices.voice_installed(code):
        return code, "ready"
    if voices.last_error(code) and not voices.downloading(code):
        return code, "failed"
    return code, "downloading" if voices.start_download(code) else "failed"


def is_devanagari(sentence: str) -> bool:
    return any("\u0900" <= ch <= "\u097f" for ch in sentence)


def talk_endpointer() -> Endpointer:
    """End-of-turn detection tuned for a person talking to the app.

    The phone defaults end a turn after 0.7 s of silence and accept 0.16 s of
    sound as speech, which split a thinking pause or an "uh…" into its own turn
    (each one interrupting the reply). Talk waits 1 s, needs 0.3 s of speech,
    and accepts quieter microphones such as AirPods (16 kHz, 20 ms frames)."""
    return Endpointer(end_frames=50, min_speech_frames=15, min_rms=200.0, noise_ratio=2.5)


_FILLERS = frozenset({
    "a", "ah", "aah", "eh", "er", "erm", "hm", "hmm", "huh", "mhm", "mm", "mmm",
    "oh", "uh", "uhh", "uhm", "um", "umm",
})


def is_filler(text: str) -> bool:
    """True when a transcript is only hesitation sounds ("Uh mmm", "Mm.")."""
    import re

    words = re.findall(r"[^\W\d_]+", text.lower())
    return bool(words) and all(w in _FILLERS for w in words)


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
    #: An Ollama model to answer with; empty uses the app's language model.
    model: str = ""
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
            if voice and not engine:
                # A cloned voice needs a commercial-use cloning engine.
                engine = cloning_voice_engine()
                unavailable = "" if engine else "clone"
            elif engine in NON_COMMERCIAL_ENGINES or (
                not engine and active_tts_engine() in NON_COMMERCIAL_ENGINES
            ):
                engine = ""
                unavailable = "clone" if voice else "voice"
        return cls(
            instructions=_s("instructions", MAX_INSTRUCTIONS_CHARS) or DEFAULT_INSTRUCTIONS,
            voice=voice,
            engine=engine,
            language=_s("language", 40),
            model=_s("model", 120),
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
        self._vad = talk_endpointer()
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
            if is_filler(text):
                continue  # a lone "uh"/"mm" while the user thinks is not a turn
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
                sentence = strip_emoji(sentence).strip()
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
        "language": "The voice can't speak this language yet, so that part is text only. "
                    "Install Indic-TTS in Model Catalogue to hear Indian languages.",
        "download": "This language's voice didn't download, so replies stay in text. "
                    "Check the connection, then start a new call to try again.",
    }

    async def _voice_unavailable(self, reason: str) -> None:
        if reason in self._warned:
            return
        self._warned.add(reason)
        await self._emit({"type": "error", "code": f"voice_unavailable_{reason}",
                          "message": self._UNAVAILABLE[reason]})

    async def _voice_downloading(self, code: str) -> None:
        if f"download:{code}" in self._warned:
            return
        self._warned.add(f"download:{code}")
        await self._emit({"type": "error", "code": "voice_downloading",
                          "message": "Downloading this language's voice (about 1.5 GB, once). "
                                     "Replies stay in text until it's ready."})

    def _engine(self) -> str:
        """The voice engine for this session: the client's pick, else (for a
        session that didn't ask for commercial-use voices only) OmniVoice for
        an Indian language, since the light voice is English only."""
        if self.config.engine or self.config.commercial_only:
            return self.config.engine
        return multilingual_engine() if is_indian_language(self.config.language) else ""

    async def _speak(self, sentence: str) -> None:
        await self._set_state("speaking")
        engine, language = self._engine(), self.config.language
        if (
            self.config.hindi_engine
            and is_devanagari(sentence)
            and (indian_language_code(language) in (None, "hi") or not self.config.multilingual_engine)
        ):
            # Kokoro reads Devanagari as Hindi; Marathi, Nepali and Sanskrit
            # go to the multilingual engine below when one is installed.
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
        if engine == INDIC_TTS_ENGINE:
            code, state = indic_tts_voice(sentence, language)
            if not code and self.config.hindi_engine and is_devanagari(sentence):
                # Nepali, Sanskrit and unnamed Devanagari: Kokoro reads it as Hindi.
                engine, language = self.config.hindi_engine, "hi"
            elif not code:
                await self._voice_unavailable("language")
                return
            elif state == "downloading":
                await self._voice_downloading(code)
                return
            elif state == "failed":
                await self._voice_unavailable("download")
                return
        async for pcm in self.deps.render(
            sentence, voice=self.config.voice, engine=engine, language=language
        ):
            if pcm.size == 0:
                continue
            now = self._clock()
            self._playback_until = max(self._playback_until, now) + pcm.size / OUTPUT_SAMPLE_RATE
            await self._emit(pcm.astype("<i2").tobytes())
