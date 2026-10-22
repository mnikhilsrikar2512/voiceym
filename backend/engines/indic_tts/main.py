"""indic-tts sidecar: AI4Bharat Indic-TTS on coqui-tts, in the engine's own venv.

Launched as ``<engine venv python> main.py`` by IndicTTSBackend. The venv holds
coqui-tts (the maintained Coqui TTS) on CPU PyTorch; this file imports nothing
from the app except the standard-library language table next to it.

Each language is a FastPitch + HiFi-GAN pair the app has already downloaded
into ``<install>/voices/<code>/``. The first load of a voice drops the
training state (optimizer, scaler) from its checkpoints, which shrinks them
from about 1.5 GB to about 0.5 GB and keeps memory low on later loads.

Wire protocol: identical to the other sidecars (engines/chatterbox/main.py).
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indic_tts_languages import LANGUAGES, detect_language  # noqa: E402

MAX_FRAME_BYTES = 64 * 1024 * 1024
#: Must match IndicTTSBackend.sample_rate (the models' training rate).
SAMPLE_RATE = 22_050
_HEARTBEAT_S = 5.0
#: Keys a trained checkpoint carries that inference never reads.
_TRAINING_KEYS = ("optimizer", "scaler")

_SYNTH: dict[str, object] = {}
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


def _voices_root() -> Path:
    install = os.environ.get("OMNIVOICE_INDIC_TTS_DIR")
    root = Path(install) if install else Path(sys.prefix).resolve().parent
    return root / "voices"


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


def _strip_training_state(checkpoint: Path) -> None:
    import torch  # noqa: PLC0415

    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if not any(key in state for key in _TRAINING_KEYS):
        return
    for key in _TRAINING_KEYS:
        state.pop(key, None)
    temporary = checkpoint.with_suffix(".tmp")
    torch.save(state, temporary)
    os.replace(temporary, checkpoint)


def _patched_config(voice: Path) -> Path:
    """The release config names its speaker file by a training-machine path."""
    config = json.loads((voice / "fastpitch" / "config.json").read_text(encoding="utf-8"))
    speakers = str(voice / "fastpitch" / "speakers.pth")
    config["speakers_file"] = speakers
    config.setdefault("model_args", {})["speakers_file"] = speakers
    patched = voice / "fastpitch" / "config.voiceym.json"
    patched.write_text(json.dumps(config), encoding="utf-8")
    return patched


def _synthesizer(stdout, code: str):
    if code in _SYNTH:
        return _SYNTH[code]
    voice = _voices_root() / code
    if not (voice / "fastpitch" / "best_model.pth").is_file():
        raise FileNotFoundError(f"The {LANGUAGES[code][0]} voice isn't downloaded yet.")
    # One voice in memory at a time: a conversation stays in one language,
    # and each voice holds a few hundred MB.
    _SYNTH.clear()
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
    with _heartbeat(stdout, "loading_model"):
        from TTS.utils.synthesizer import Synthesizer  # type: ignore[import-not-found]  # noqa: PLC0415

        for part in ("fastpitch", "hifigan"):
            _strip_training_state(voice / part / "best_model.pth")
        _SYNTH[code] = Synthesizer(
            tts_checkpoint=str(voice / "fastpitch" / "best_model.pth"),
            tts_config_path=str(_patched_config(voice)),
            tts_speakers_file=str(voice / "fastpitch" / "speakers.pth"),
            vocoder_checkpoint=str(voice / "hifigan" / "best_model.pth"),
            vocoder_config=str(voice / "hifigan" / "config.json"),
            use_cuda=False,
        )
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _SYNTH[code]


def _speaker(synth, voice: object) -> "str | None":
    manager = getattr(synth.tts_model, "speaker_manager", None)
    names = list(getattr(manager, "speaker_names", None) or [])
    if not names:
        return None
    wanted = voice.strip().lower() if isinstance(voice, str) else ""
    if wanted in names:
        return wanted
    return "female" if "female" in names else names[0]


def _handle_synthesize(msg: dict, stdout) -> None:
    import numpy as np  # noqa: PLC0415

    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")
    code = detect_language(text, msg.get("language"))
    if code is None:
        raise ValueError("Indic-TTS can't speak this text's language.")
    synth = _synthesizer(stdout, code)
    wav = synth.tts(text, speaker_name=_speaker(synth, msg.get("voice")))
    arr = np.clip(np.asarray(wav, dtype=np.float32).reshape(-1), -1.0, 1.0)
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
    _send(stdout, {"op": "ready", "engine": "indic-tts", "sample_rate": SAMPLE_RATE})
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
                _send(stdout, {"op": "pong", "vram_mb": 0.0})
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
