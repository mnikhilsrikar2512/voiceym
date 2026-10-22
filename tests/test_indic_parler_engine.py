"""Indic Parler-TTS: the sidecar picks the language and speaker by itself.

The sidecar's model code runs only in the engine's own venv; its language and
speaker choice is plain Python and is checked here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "backend" / "engines" / "indic_parler" / "main.py"


@pytest.fixture(scope="module")
def sidecar():
    spec = importlib.util.spec_from_file_location("indic_parler_sidecar", SIDECAR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("నమస్కారం", None, "te"),
        ("வணக்கம்", "auto", "ta"),
        ("नमस्ते", "", "hi"),
        ("नमस्कार", "Marathi", "mr"),
        ("నమస్కార", "kn", "kn"),
        ("Hello there", None, "en"),
        ("نمستے", None, "ur"),
    ],
)
def test_language_comes_from_the_name_or_the_script(sidecar, text, language, expected):
    assert sidecar.detect_language(text, language) == expected


def test_description_names_a_speaker_of_that_language(sidecar):
    assert sidecar.description_for("te").startswith("Prakash ")
    assert sidecar.description_for("te", "lalitha").startswith("Lalitha ")
    # A speaker from another language is not used.
    assert sidecar.description_for("ta", "Rohit").startswith("Jaya ")
    # Languages without a named speaker still get a clear voice.
    assert sidecar.description_for("ur").startswith("A female speaker ")


def test_backend_is_registered_and_commercial_friendly():
    from services.tts_backend import get_backend_class

    cls = get_backend_class("indic-parler")
    assert cls.id == "indic-parler"
    assert not cls.supports_cloning
    assert {"ta", "te", "kn", "ml", "bn", "mr", "gu", "or", "ur"} <= set(cls().supported_languages)
    from services import voice_agent as va

    assert "indic-parler" not in va.NON_COMMERCIAL_ENGINES


def test_sample_rate_matches_the_sidecar(sidecar):
    from engines.indic_parler import SAMPLE_RATE

    assert sidecar.SAMPLE_RATE == SAMPLE_RATE


def test_install_spec_pins_source_and_weights():
    from engines.indic_parler import VENV_ENV_VAR, WEIGHTS_REPO_ID, WEIGHTS_REVISION, WEIGHTS_SUBDIR
    from services.sidecar_install import SPECS

    spec = SPECS["indic-parler"]
    assert spec.env_var == VENV_ENV_VAR
    assert (spec.weights_repo_id, spec.weights_revision, spec.weights_subdir) == (
        WEIGHTS_REPO_ID, WEIGHTS_REVISION, WEIGHTS_SUBDIR)
    assert len(spec.source_revision) == 40 and spec.source_revision in spec.tarball_url
