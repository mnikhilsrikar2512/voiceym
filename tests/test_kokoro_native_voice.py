"""Kokoro reads every language in its American default voice unless told
otherwise, so Hindi came out with an English speaker's accent. With a language
and no explicit voice, the adapter now picks Kokoro's native voice for it."""
import importlib

import numpy as np
import pytest


class _Result:
    def __init__(self):
        self.audio = np.zeros(16, dtype="float32")


class _Model:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        yield _Result()


@pytest.fixture
def kokoro(monkeypatch):
    tts = importlib.import_module("services.tts_backend")
    be = tts.MLXAudioBackend.__new__(tts.MLXAudioBackend)
    be._model_id = tts.MLXAudioBackend.CURATED_MODELS["kokoro"]
    be._sr = 24000
    be._model = _Model()
    monkeypatch.setattr(be, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(tts, "resolve_kokoro_lang_code", lambda lang: {"hi": "h", "en": "a"}[lang])
    return be


def test_hindi_without_a_voice_uses_the_native_hindi_speaker(kokoro):
    kokoro.generate("नमस्ते", language="hi")
    assert kokoro._model.calls[-1]["lang_code"] == "h"
    assert kokoro._model.calls[-1]["voice"] == "hf_alpha"


def test_an_explicit_voice_is_never_replaced(kokoro):
    kokoro.generate("Hello", language="en", voice="am_adam")
    assert kokoro._model.calls[-1]["voice"] == "am_adam"
