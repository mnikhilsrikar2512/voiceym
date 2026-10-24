"""#2938: engines with built-in preset voices expose them and /generate honours the pick."""
import importlib
import os

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _tts():
    return importlib.import_module("services.tts_backend")


def test_catalogue_lists_kittentts_presets():
    tts = _tts()
    entry = {e["id"]: e for e in tts.list_backends()}["kittentts"]
    assert entry["preset_voices"] == tts.preset_voices_of(tts.KittenTTSBackend)
    assert entry["preset_voices"]
    assert entry["default_voice"] == tts.KittenTTSBackend.DEFAULT_VOICE


def test_engines_without_presets_list_none():
    entries = {e["id"]: e for e in _tts().list_backends()}
    assert entries["voxcpm2"]["preset_voices"] == []
    assert entries["voxcpm2"]["default_voice"] is None


def _engine():
    class _Fake(_tts().TTSBackend):
        id = "fake-presets-2938"
        display_name = "Preset engine (test)"
        supports_voice_design = False
        supports_cloning = False
        applies_own_mastering = False
        gpu_compat = ("cpu",)
        PRESET_VOICES = ["A1", "B2"]
        DEFAULT_VOICE = "A1"
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append(kw)
            return torch.zeros(1, 2400)

    _Fake.calls = []
    return _Fake


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def test_generate_forwards_the_picked_preset(client, monkeypatch):
    fake = _engine()
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    res = client.post("/generate", data={"text": "Hello", "engine": fake.id, "preset_voice": "B2"})
    assert res.status_code == 200, res.text
    assert fake.calls and fake.calls[0]["voice"] == "B2"


def test_generate_without_a_pick_leaves_the_engine_default(client, monkeypatch):
    fake = _engine()
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    assert client.post("/generate", data={"text": "Hello", "engine": fake.id}).status_code == 200
    assert "voice" not in fake.calls[0]


def test_generate_rejects_an_unknown_preset(client, monkeypatch):
    fake = _engine()
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    res = client.post("/generate", data={"text": "Hello", "engine": fake.id, "preset_voice": "Z9"})
    assert res.status_code == 422
    assert "A1" in res.json()["detail"]
    assert fake.calls == []
