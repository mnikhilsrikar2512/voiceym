"""Chatterbox (Resemble AI, MIT): commercial-use English voice cloning.

Upstream pins torch 2.6 and transformers 5.2, so it cannot share Voiceym's
environment. The one-click installer clones a reviewed commit into
``DATA_DIR/engines/chatterbox/`` with its own venv, and this class runs it in
a killable sidecar (``main.py``) the way MOSS-TTS-Nano runs. Weights
(``ResembleAI/chatterbox``, MIT) download on the first synthesis. Chatterbox
marks its output with Resemble's imperceptible Perth watermark.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from services.subprocess_backend import SubprocessBackend

VENV_ENV_VAR = "OMNIVOICE_CHATTERBOX_DIR"


def own_venv_python() -> "Path | None":
    """The venv the one-click installer made for Chatterbox, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(VENV_ENV_VAR)


class ChatterboxBackend(SubprocessBackend):
    """Chatterbox in a killable sidecar running the engine's own venv."""

    id = "chatterbox"
    display_name = "Chatterbox (English voice cloning, MIT)"
    supports_cloning = True
    supports_voice_design = False
    gpu_compat = ("cuda", "mps", "cpu")
    language_display_names = {"en": "English"}
    _DEFAULT_SAMPLE_RATE = 24_000

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if own_venv_python() is None:
            return False, "Chatterbox isn't installed yet. Install it from Model Catalogue."
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        py = own_venv_python()
        if py is None:
            raise RuntimeError(
                "Chatterbox's environment is missing. Reinstall it from Model Catalogue."
            )
        return py

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # The first synthesis downloads ~3 GB of weights; the sidecar heartbeats
        # progress frames meanwhile, and each re-arms this deadline.
        try:
            v = float(os.environ.get("OMNIVOICE_CHATTERBOX_RECV_TIMEOUT_S", "900"))
        except (TypeError, ValueError):
            return 900.0
        if not math.isfinite(v):
            return 900.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        return ["en"]


__all__ = ["ChatterboxBackend", "VENV_ENV_VAR", "own_venv_python"]
