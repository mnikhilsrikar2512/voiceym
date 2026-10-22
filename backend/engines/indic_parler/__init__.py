"""Indic Parler-TTS (AI4Bharat, Apache-2.0): commercial-use Indian languages.

Speaks 21 languages (Assamese, Bengali, Bodo, Dogri, English, Gujarati, Hindi,
Kannada, Konkani, Maithili, Malayalam, Manipuri, Marathi, Nepali, Odia,
Sanskrit, Santali, Sindhi, Tamil, Telugu and Urdu, plus Punjabi and
Chhattisgarhi unofficially) with named per-language speakers.

Upstream ``parler_tts`` pins transformers 4.46.1, so it cannot share
Voiceym's environment. The one-click installer clones a reviewed commit into
``DATA_DIR/engines/indic-parler/`` with its own venv and downloads the pinned
weights (``ai4bharat/indic-parler-tts``, gated: the user accepts its terms on
Hugging Face and adds a token first). This class runs it in a killable
sidecar (``main.py``), like Chatterbox.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from services.subprocess_backend import SubprocessBackend

VENV_ENV_VAR = "OMNIVOICE_INDIC_PARLER_DIR"
WEIGHTS_REPO_ID = "ai4bharat/indic-parler-tts"
WEIGHTS_REVISION = "66e3f72fc0637aeec2b5913a7f899ea5465ebe3c"
WEIGHTS_SUBDIR = "weights"
#: The DAC codec's native rate; the sidecar always returns audio at this rate.
SAMPLE_RATE = 44_100

LANGUAGE_NAMES = {
    "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri",
    "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri",
    "mr": "Marathi", "ne": "Nepali", "or": "Odia", "sa": "Sanskrit",
    "sat": "Santali", "sd": "Sindhi", "ta": "Tamil", "te": "Telugu",
    "ur": "Urdu", "pa": "Punjabi", "hne": "Chhattisgarhi",
}


def own_venv_python() -> "Path | None":
    """The venv the one-click installer made for Indic Parler-TTS, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(VENV_ENV_VAR)


def weights_dir() -> "Path | None":
    """Where the installer put the pinned weights, if the engine is installed."""
    py = own_venv_python()
    if py is None:
        return None
    from services.sidecar_install import SPECS, managed_checkout

    path = managed_checkout(SPECS["indic-parler"]) / WEIGHTS_SUBDIR
    return path if (path / "config.json").is_file() else None


class IndicParlerBackend(SubprocessBackend):
    """Indic Parler-TTS in a killable sidecar running the engine's own venv."""

    id = "indic-parler"
    display_name = "Indic Parler-TTS (21 Indian languages, Apache-2.0)"
    supports_cloning = False
    supports_voice_design = False
    gpu_compat = ("cuda", "mps", "cpu")
    language_display_names = dict(LANGUAGE_NAMES)

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if own_venv_python() is None:
            return False, "Indic Parler-TTS isn't installed yet. Install it from Model Catalogue."
        if weights_dir() is None:
            return False, "Indic Parler-TTS weights are missing. Reinstall it from Model Catalogue."
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        py = own_venv_python()
        if py is None:
            raise RuntimeError(
                "Indic Parler-TTS's environment is missing. Reinstall it from Model Catalogue."
            )
        return py

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # Loading 0.9B parameters from disk on a CPU can take minutes; the
        # sidecar heartbeats progress frames meanwhile, and each re-arms this.
        try:
            v = float(os.environ.get("OMNIVOICE_INDIC_PARLER_RECV_TIMEOUT_S", "600"))
        except (TypeError, ValueError):
            return 600.0
        if not math.isfinite(v):
            return 600.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        return list(LANGUAGE_NAMES)


__all__ = [
    "IndicParlerBackend", "LANGUAGE_NAMES", "SAMPLE_RATE", "VENV_ENV_VAR",
    "WEIGHTS_REPO_ID", "WEIGHTS_REVISION", "WEIGHTS_SUBDIR", "own_venv_python", "weights_dir",
]
