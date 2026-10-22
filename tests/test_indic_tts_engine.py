"""Indic-TTS: Indian languages with no account.

The sidecar's model code runs only in the engine's own venv. What is checked
here is plain Python: which language a sentence is in, and how a voice is
downloaded, verified and unpacked.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

from engines.indic_tts import voices
from engines.indic_tts.indic_tts_languages import LANGUAGES, RELEASE_URL, detect_language

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "backend" / "engines" / "indic_tts" / "main.py"


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("నమస్కారం", None, "te"),
        ("வணக்கம்", "auto", "ta"),
        ("নমস্কার", None, "bn"),
        ("নমস্কাৰ", "Assamese", "as"),
        ("नमस्कार", "Marathi", "mr"),
        ("नमस्ते", "", None),  # Hindi stays with Kokoro
        ("नमस्ते", "Hindi", None),
        ("Hello there", None, None),
        ("نمستے", None, None),
        ("ꯍꯥꯏ", None, "mni"),
    ],
)
def test_language_comes_from_the_name_or_the_script(text, language, expected):
    assert detect_language(text, language) == expected


def test_every_language_has_a_checksum_and_size():
    assert set(LANGUAGES) == {"as", "bn", "brx", "gu", "kn", "ml", "mni", "mr", "or", "pa", "ta", "te"}
    for name, sha256, size in LANGUAGES.values():
        assert name and len(sha256) == 64 and int(sha256, 16) >= 0
        assert 1_000_000_000 < size < 2_000_000_000
    assert RELEASE_URL.startswith("https://github.com/AI4Bharat/Indic-TTS/releases/download/")


def test_sidecar_matches_the_backend():
    spec = importlib.util.spec_from_file_location("indic_tts_sidecar", SIDECAR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from engines.indic_tts import SAMPLE_RATE

    assert module.SAMPLE_RATE == SAMPLE_RATE


def test_backend_is_registered_and_commercial_friendly():
    from services import voice_agent as va
    from services.sidecar_install import SPECS
    from services.tts_backend import get_backend_class

    cls = get_backend_class("indic-tts")
    assert cls.id == "indic-tts" and not cls.supports_cloning
    assert set(cls().supported_languages) == set(LANGUAGES)
    assert "indic-tts" not in va.NON_COMMERCIAL_ENGINES
    spec = SPECS["indic-tts"]
    assert spec.env_var == "OMNIVOICE_INDIC_TTS_DIR" and not spec.has_source
    assert spec.install_args == ("coqui-tts[cpu]==0.28.0",)


def _fake_release(code: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for rel in voices.VOICE_FILES:
            bundle.writestr(f"{code}/{rel}", rel)
        bundle.writestr(f"{code}/fastpitch/trainer_0_log.txt", "not kept")
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_download_verifies_and_keeps_only_what_inference_needs(tmp_path, monkeypatch):
    payload = _fake_release("te")
    monkeypatch.setenv("OMNIVOICE_INDIC_TTS_DIR", str(tmp_path))
    monkeypatch.setitem(LANGUAGES, "te", ("Telugu", hashlib.sha256(payload).hexdigest(), len(payload)))
    urls = []

    def opener(url, timeout):
        urls.append(url)
        return _Response(payload)

    assert not voices.voice_installed("te")
    target = voices.download_voice("te", opener=opener)
    assert urls == [f"{RELEASE_URL}/te.zip"]
    assert voices.voice_installed("te")
    kept = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
    assert kept == sorted([*voices.VOICE_FILES, ".voiceym-complete"])
    assert [p.name for p in (tmp_path / "voices").iterdir()] == ["te"]


def test_a_corrupted_download_installs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_INDIC_TTS_DIR", str(tmp_path))
    monkeypatch.setitem(LANGUAGES, "te", ("Telugu", "0" * 64, 1))
    with pytest.raises(RuntimeError, match="corrupted"):
        voices.download_voice("te", opener=lambda url, timeout: _Response(_fake_release("te")))
    assert not voices.voice_installed("te")
    assert list((tmp_path / "voices").iterdir()) == []
