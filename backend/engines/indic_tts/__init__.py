"""Indic-TTS (AI4Bharat, MIT): Indian languages with no account.

FastPitch + HiFi-GAN voices for Assamese, Bengali, Bodo, Gujarati, Kannada,
Malayalam, Manipuri, Marathi, Odia, Punjabi, Tamil and Telugu, each with a
female and a male speaker. Everything downloads from GitHub: the one-click
installer makes a venv with coqui-tts (MPL-2.0) on CPU PyTorch, and each
language's voice (~1.5 GB download, ~0.5 GB kept) downloads the first time
it is needed (``voices.py``). This class runs it in a killable sidecar
(``main.py``), like Chatterbox.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from services.subprocess_backend import SubprocessBackend
from services.tts_backend import TTSInputError

from . import voices
from .indic_tts_languages import LANGUAGES, detect_language

VENV_ENV_VAR = "OMNIVOICE_INDIC_TTS_DIR"
SAMPLE_RATE = 22_050


def own_venv_python() -> "Path | None":
    """The venv the one-click installer made for Indic-TTS, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(VENV_ENV_VAR)


class IndicTTSBackend(SubprocessBackend):
    """Indic-TTS in a killable sidecar running the engine's own venv."""

    id = "indic-tts"
    display_name = "Indic-TTS (12 Indian languages, MIT, no account)"
    supports_cloning = False
    supports_voice_design = False
    gpu_compat = ("cpu",)
    language_display_names = {code: name for code, (name, _, _) in LANGUAGES.items()}

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if own_venv_python() is None:
            return False, "Indic-TTS isn't installed yet. Install it from Model Catalogue."
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        py = own_venv_python()
        if py is None:
            raise RuntimeError(
                "Indic-TTS's environment is missing. Reinstall it from Model Catalogue."
            )
        return py

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # The first load of a voice rewrites its checkpoints without their
        # training state; the sidecar heartbeats meanwhile.
        try:
            v = float(os.environ.get("OMNIVOICE_INDIC_TTS_RECV_TIMEOUT_S", "300"))
        except (TypeError, ValueError):
            return 300.0
        if not math.isfinite(v):
            return 300.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        return list(LANGUAGES)

    def generate(self, text: str, **kw):
        code = detect_language(text, kw.get("language"))
        if code is None:
            raise TTSInputError(
                "Indic-TTS speaks Assamese, Bengali, Bodo, Gujarati, Kannada, Malayalam, "
                "Manipuri, Marathi, Odia, Punjabi, Tamil and Telugu; this text is in none of them."
            )
        if not voices.voice_installed(code):
            name = LANGUAGES[code][0]
            voices.start_download(code)
            raise TTSInputError(
                f"Downloading the {name} voice (about 1.5 GB, once). Try again in a few minutes."
            )
        return super().generate(text, **kw)


__all__ = ["IndicTTSBackend", "SAMPLE_RATE", "VENV_ENV_VAR", "own_venv_python"]
