"""Kokoro (ONNX) engine: language routing, voices and chunking, with the ONNX
session and phonemizers faked so no model is needed."""
import importlib

import numpy as np
import pytest


class _Session:
    def __init__(self):
        self.feeds = []

    def run(self, _outputs, feeds):
        self.feeds.append(feeds)
        return [np.zeros((1, 2400), dtype=np.float32)]


@pytest.fixture
def kokoro(monkeypatch):
    mod = importlib.import_module("engines.kokoro")
    be = mod.KokoroBackend()
    be._session = _Session()
    be._vocab = {ch: i + 1 for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz .")}
    be._dir = "/unused"
    calls = []

    def phonemizer(language):
        calls.append(language)
        return lambda text: text.lower().encode("ascii", "ignore").decode() or "a"

    monkeypatch.setattr(be, "_phonemizer", phonemizer)
    monkeypatch.setattr(be, "_voice", lambda name: (calls.append(name), np.zeros((511, 1, 256), np.float32))[1])
    be.calls = calls
    return be


def test_auto_language_follows_the_script(kokoro):
    kokoro.generate("Hello there.")
    kokoro.generate("नमस्ते")
    assert kokoro.calls == ["en", "af_heart", "hi", "hf_alpha"]


def test_named_language_and_preset_voice(kokoro):
    kokoro.generate("Hola amigo.", language="Spanish")
    kokoro.generate("Hello.", language="en", voice="bm_george")
    assert kokoro.calls == ["es", "ef_dora", "en", "bm_george"]


def test_unsupported_language_is_refused(kokoro):
    with pytest.raises(ValueError, match="doesn't support"):
        kokoro.generate("Hallo", language="de")


def test_long_text_is_split_within_the_model_limit(kokoro):
    wav = kokoro.generate(("word " * 300).strip() + ". Next sentence.")
    lengths = [f["input_ids"].shape[1] - 2 for f in kokoro._session.feeds]
    assert len(lengths) >= 3 and max(lengths) <= 510
    assert wav.shape == (1, 2400 * len(lengths))


def test_kokoro_is_the_default_engine(monkeypatch):
    tts = importlib.import_module("services.tts_backend")
    from core import prefs

    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "get", lambda key, default=None: default)
    assert tts.active_backend_id() == "kokoro"
    assert tts.get_backend_class("kokoro").is_available()[0]
