"""indic-parler sidecar: Indic Parler-TTS in the engine's own venv (one-click install).

Launched as ``<engine venv python> main.py`` by IndicParlerBackend. The venv
holds the reviewed parler-tts checkout, installed editable, and its pinned
dependencies, so this file imports nothing from the app. The installer has
already downloaded the pinned weights into ``<checkout>/weights``; the
checkout is the directory ``OMNIVOICE_INDIC_PARLER_DIR`` names (else the
venv's parent).

Wire protocol: identical to the other sidecars (engines/chatterbox/main.py).
Progress frames are sent while the model loads; later calls send none, so the
parent's watchdog still catches a generation that wedges.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import struct
import sys
import threading
import traceback
from pathlib import Path

MAX_FRAME_BYTES = 64 * 1024 * 1024
#: Must match IndicParlerBackend.sample_rate (the DAC codec's native rate).
SAMPLE_RATE = 44_100
_HEARTBEAT_S = 5.0
#: The description encoder's tokenizer (the model config names it).
_DESCRIPTION_TOKENIZER = "google/flan-t5-large"

#: Script ranges -> language, for Auto: the reply's own letters say which
#: language to speak. Devanagari is read as Hindi unless a language is named.
_SCRIPTS = (
    (0x0900, 0x097F, "hi"), (0x0980, 0x09FF, "bn"), (0x0A00, 0x0A7F, "pa"),
    (0x0A80, 0x0AFF, "gu"), (0x0B00, 0x0B7F, "or"), (0x0B80, 0x0BFF, "ta"),
    (0x0C00, 0x0C7F, "te"), (0x0C80, 0x0CFF, "kn"), (0x0D00, 0x0D7F, "ml"),
    (0x0600, 0x06FF, "ur"), (0xABC0, 0xABFF, "mni"), (0x1C50, 0x1C7F, "sat"),
)

#: Recommended speakers per language (ai4bharat/indic-parler-tts model card).
SPEAKERS = {
    "as": ("Amit", "Sita"), "bn": ("Arjun", "Aditi"), "brx": ("Bikram", "Maya"),
    "hne": ("Bhanu", "Champa"), "doi": ("Karan",), "en": ("Thoma", "Mary"),
    "gu": ("Yash", "Neha"), "hi": ("Rohit", "Divya"), "kn": ("Suresh", "Anu"),
    "ml": ("Anjali", "Harish"), "mni": ("Laishram", "Ranjit"), "mr": ("Sanjay", "Sunita"),
    "ne": ("Amrita",), "or": ("Manas", "Debjani"), "pa": ("Divjot", "Gurpreet"),
    "sa": ("Aryan",), "ta": ("Jaya", "Kavitha"), "te": ("Prakash", "Lalitha"),
}

_NAMES = {
    "assamese": "as", "bengali": "bn", "bangla": "bn", "bodo": "brx", "dogri": "doi",
    "english": "en", "gujarati": "gu", "hindi": "hi", "kannada": "kn", "konkani": "kok",
    "maithili": "mai", "malayalam": "ml", "manipuri": "mni", "marathi": "mr",
    "nepali": "ne", "odia": "or", "oriya": "or", "sanskrit": "sa", "santali": "sat",
    "sindhi": "sd", "tamil": "ta", "telugu": "te", "urdu": "ur", "punjabi": "pa",
    "panjabi": "pa", "chhattisgarhi": "hne",
}

_STYLE = (
    "speaks at a moderate pace with a clear, natural and friendly tone. "
    "The recording is of very high quality, with the speaker's voice sounding clear and very close up."
)

_MODEL = None
_TOKENIZER = None
_DESC_TOKENIZER = None
_send_lock = threading.Lock()


def _send(stream, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    with _send_lock:
        stream.write(struct.pack("!I", len(body)))
        stream.write(body)
        stream.flush()


def _recv(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    (n,) = struct.unpack("!I", header)
    if n > MAX_FRAME_BYTES:
        raise IOError(f"frame too large: {n}")
    body = bytearray()
    while len(body) < n:
        chunk = stream.read(n - len(body))
        if not chunk:
            raise IOError("short read")
        body.extend(chunk)
    return json.loads(bytes(body).decode("utf-8"))


def detect_language(text: str, language: object = None) -> str:
    """A named language wins; otherwise the script of the text decides, and
    Latin text is English."""
    if isinstance(language, str):
        value = language.strip().lower()
        if value in _NAMES:
            return _NAMES[value]
        head = value.replace("_", "-").split("-", 1)[0]
        if head and head != "auto" and head in set(_NAMES.values()):
            return head
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for lo, hi, code in _SCRIPTS:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    return max(counts, key=counts.get) if counts else "en"


def description_for(language: str, voice: object = None) -> str:
    """The style prompt: a named speaker for the language (the user's pick when
    it is one of them), else a generic clear voice."""
    speakers = SPEAKERS.get(language, ())
    name = voice.strip() if isinstance(voice, str) else ""
    match = next((s for s in speakers if s.lower() == name.lower()), None) if name else None
    speaker = match or (speakers[0] if speakers else "")
    if speaker:
        return f"{speaker} {_STYLE}"
    return f"A female speaker {_STYLE}"


def _weights_dir() -> Path:
    checkout = os.environ.get("OMNIVOICE_INDIC_PARLER_DIR")
    root = Path(checkout) if checkout else Path(sys.prefix).resolve().parent
    return root / "weights"


def _device():
    import torch  # noqa: PLC0415

    if torch.cuda.is_available():
        return "cuda", torch.float16
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps", torch.float16
    return "cpu", torch.float32


def _measure_vram_mb() -> float:
    try:
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            return float(torch.cuda.memory_allocated()) / (1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


@contextlib.contextmanager
def _heartbeat(stdout, stage: str):
    stop = threading.Event()

    def beat() -> None:
        pct = 1
        while not stop.wait(_HEARTBEAT_S):
            pct = min(pct + 1, 99)
            _send(stdout, {"op": "progress", "stage": stage, "percent": pct})

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_HEARTBEAT_S + 1)


def _load(stdout):
    global _MODEL, _TOKENIZER, _DESC_TOKENIZER
    if _MODEL is None:
        _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
        with _heartbeat(stdout, "loading_model"):
            from parler_tts import ParlerTTSForConditionalGeneration  # type: ignore[import-not-found]  # noqa: PLC0415
            from transformers import AutoTokenizer  # noqa: PLC0415

            path = str(_weights_dir())
            device, dtype = _device()
            model = ParlerTTSForConditionalGeneration.from_pretrained(path, torch_dtype=dtype)
            _MODEL = model.to(device).eval()
            _TOKENIZER = AutoTokenizer.from_pretrained(path)
            _DESC_TOKENIZER = AutoTokenizer.from_pretrained(_DESCRIPTION_TOKENIZER)
        _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _MODEL


def _resample(arr, rate: int):
    import numpy as np  # noqa: PLC0415

    if rate == SAMPLE_RATE or arr.size == 0:
        return arr
    n = int(round(arr.size * SAMPLE_RATE / rate))
    return np.interp(np.linspace(0, arr.size - 1, n), np.arange(arr.size), arr).astype(np.float32)


def _handle_synthesize(msg: dict, stdout) -> None:
    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415

    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")
    model = _load(stdout)
    language = detect_language(text, msg.get("language"))
    description = description_for(language, msg.get("voice"))
    device = model.device
    desc = _DESC_TOKENIZER(description, return_tensors="pt").to(device)
    prompt = _TOKENIZER(text, return_tensors="pt").to(device)
    with torch.inference_mode():
        out = model.generate(
            input_ids=desc.input_ids,
            attention_mask=desc.attention_mask,
            prompt_input_ids=prompt.input_ids,
            prompt_attention_mask=prompt.attention_mask,
        )
    arr = np.asarray(out.detach().cpu().float().numpy(), dtype=np.float32).reshape(-1)
    arr = _resample(arr, int(model.config.sampling_rate))
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    _send(stdout, {"op": "audio", "audio_pcm_b64": base64.b64encode(pcm).decode("ascii"),
                   "sample_rate": SAMPLE_RATE, "n_samples": int(arr.size)})


def main() -> int:
    stdin = sys.stdin.buffer
    # Frames go down a private fd; fd 1 points at stderr so library prints
    # can't interleave with the length-prefixed frames (#1428).
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")
    _send(stdout, {"op": "ready", "engine": "indic-parler", "sample_rate": SAMPLE_RATE})
    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:  # noqa: BLE001
            _send(stdout, {"op": "error", "stage": "recv", "message": f"{type(exc).__name__}: {exc}",
                           "traceback": traceback.format_exc()})
            return 1
        if msg is None:
            return 0
        op = msg.get("op") if isinstance(msg, dict) else None
        try:
            if op == "ping":
                _send(stdout, {"op": "pong", "vram_mb": _measure_vram_mb()})
            elif op == "synthesize":
                _handle_synthesize(msg, stdout)
            elif op == "shutdown":
                return 0
            else:
                _send(stdout, {"op": "error", "stage": "dispatch", "message": f"unknown op: {op!r}"})
        except Exception as exc:  # noqa: BLE001
            _send(stdout, {"op": "error", "stage": op or "unknown", "message": f"{type(exc).__name__}: {exc}",
                           "traceback": traceback.format_exc()})


if __name__ == "__main__":
    sys.exit(main())
