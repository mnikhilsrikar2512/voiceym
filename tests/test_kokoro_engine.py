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


def test_arm_cpus_prefer_the_half_precision_graph(monkeypatch):
    import platform

    mod = importlib.import_module("engines.kokoro")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert mod.preferred_graph() == mod.FAST_GRAPH_FILE
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert mod.preferred_graph() == mod.GRAPH_FILE


def _loader(monkeypatch, mod, *, cached):
    be = mod.KokoroBackend()
    opened = []
    monkeypatch.setattr(mod, "preferred_graph", lambda: mod.FAST_GRAPH_FILE)
    monkeypatch.setattr(be, "_download", lambda patterns: "/models")
    monkeypatch.setattr(mod.KokoroBackend, "_cached", staticmethod(lambda name: cached))
    monkeypatch.setattr(mod.KokoroBackend, "_open", staticmethod(lambda path: opened.append(path) or path))
    import builtins
    import io

    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open",
        lambda path, *a, **k: io.StringIO('{"model": {"vocab": {}}}')
        if str(path).endswith("tokenizer.json") else real_open(path, *a, **k),
    )
    return be, opened


def test_a_cached_fast_graph_loads_directly(monkeypatch):
    mod = importlib.import_module("engines.kokoro")
    be, opened = _loader(monkeypatch, mod, cached=True)
    upgrades = []
    monkeypatch.setattr(be, "_upgrade_in_background", upgrades.append)
    be._ensure_loaded()
    assert opened == ["/models/" + mod.FAST_GRAPH_FILE] and upgrades == []


def test_speech_starts_on_the_8bit_graph_while_the_fast_one_downloads(monkeypatch):
    mod = importlib.import_module("engines.kokoro")
    be, opened = _loader(monkeypatch, mod, cached=False)
    upgrades = []
    monkeypatch.setattr(be, "_upgrade_in_background", upgrades.append)
    be._ensure_loaded()
    assert opened == ["/models/" + mod.GRAPH_FILE]
    assert upgrades == [mod.FAST_GRAPH_FILE]


def test_the_background_upgrade_swaps_the_session_and_a_failure_keeps_it(monkeypatch):
    import threading

    import huggingface_hub

    mod = importlib.import_module("engines.kokoro")
    be = mod.KokoroBackend()
    be._session = "8bit"
    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", InlineThread)
    monkeypatch.setattr(mod.KokoroBackend, "_open", staticmethod(lambda path: "fast:" + path))
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda repo, name, revision: "/c/" + name)
    be._upgrade_in_background(mod.FAST_GRAPH_FILE)
    assert be._session == "fast:/c/" + mod.FAST_GRAPH_FILE and not be._upgrading

    be._session = "8bit"

    def offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", offline)
    be._upgrade_in_background(mod.FAST_GRAPH_FILE)
    assert be._session == "8bit" and not be._upgrading
