"""chatterbox sidecar: Chatterbox in the engine's own venv (one-click install).

Launched as ``<engine venv python> main.py`` by ChatterboxBackend. The venv
holds the reviewed upstream checkout, installed editable, and its pinned
dependencies, so this file imports nothing from the app.

Wire protocol: identical to the other sidecars (engines/moss_tts_nano_subprocess/
main.py). Progress frames are sent while the model loads, which is when the
weights download; later calls send none, so the parent's watchdog still
catches a generation that wedges.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import struct
import sys
import threading
import traceback

MAX_FRAME_BYTES = 64 * 1024 * 1024
SAMPLE_RATE = 24_000
_HEARTBEAT_S = 5.0
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

_MODEL = None
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


def _device() -> str:
    import torch  # noqa: PLC0415

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


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
    global _MODEL
    if _MODEL is None:
        _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
        with _heartbeat(stdout, "loading_model"):
            from chatterbox.tts import ChatterboxTTS  # type: ignore[import-not-found]  # noqa: PLC0415

            _MODEL = ChatterboxTTS.from_pretrained(device=_device())
        _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _MODEL


def _pcm_b64(wav) -> tuple[str, int, int]:
    import numpy as np  # noqa: PLC0415

    arr = wav.detach().cpu().float().numpy() if hasattr(wav, "detach") else np.asarray(wav)
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(arr.shape[-1]), SAMPLE_RATE


def _handle_synthesize(msg: dict, stdout) -> None:
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")
    ref_audio = msg.get("ref_audio") or None
    if ref_audio and _URL_RE.match(str(ref_audio)):
        raise ValueError("ref_audio must be a local file path; URLs are not accepted (local-first).")
    model = _load(stdout)
    kwargs = {}
    if ref_audio:
        kwargs["audio_prompt_path"] = str(ref_audio)
    wav = model.generate(text, **kwargs)
    sr = int(getattr(model, "sr", SAMPLE_RATE) or SAMPLE_RATE)
    if sr != SAMPLE_RATE:
        import torchaudio  # noqa: PLC0415

        wav = torchaudio.functional.resample(wav, sr, SAMPLE_RATE)
    pcm_b64, n_samples, rate = _pcm_b64(wav)
    _send(stdout, {"op": "audio", "audio_pcm_b64": pcm_b64, "sample_rate": rate, "n_samples": n_samples})


def main() -> int:
    stdin = sys.stdin.buffer
    # Frames go down a private fd; fd 1 points at stderr so library prints
    # can't interleave with the length-prefixed frames (#1428).
    frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(frame_fd, "wb")
    _send(stdout, {"op": "ready", "engine": "chatterbox", "sample_rate": SAMPLE_RATE})
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
