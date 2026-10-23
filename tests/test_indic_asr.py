"""Talk hears Telugu and Hindi with Whisper fine-tuned for each language when
it is installed, and with the general multilingual tier otherwise."""
import asyncio
import importlib

import numpy as np


def test_models_are_pinned_and_catalogued():
    indic = importlib.import_module("services.indic_asr")
    from services.hf_revisions import revision_for

    assert set(indic.MODELS) == {"te", "hi"}
    for repo in indic.MODELS.values():
        assert len(revision_for(repo)) == 40


def test_an_uninstalled_language_is_reported_as_such(monkeypatch):
    indic = importlib.import_module("services.indic_asr")
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: None)
    assert indic.installed("te") is False
    assert indic.installed("ta") is False  # no fine-tuned model registered


def test_talk_prefers_the_fine_tuned_model_for_its_language(monkeypatch):
    va = importlib.import_module("services.voice_agent")
    indic = importlib.import_module("services.indic_asr")
    import services.asr_backend as asr

    calls = []
    monkeypatch.setattr(indic, "installed", lambda code: code == "te")
    monkeypatch.setattr(indic, "transcribe", lambda path, code: calls.append(code) or "నమస్కారం")

    async def run_inline(_pool, fn, **_kw):
        return fn()

    monkeypatch.setattr(asr, "run_transcribe_guarded", run_inline)
    monkeypatch.setattr(va, "_multilingual_asr", lambda: (_ for _ in ()).throw(AssertionError("fallback used")))
    pcm = (np.sin(np.linspace(0, 200, 16000)) * 8000).astype(np.int16)
    assert asyncio.run(va._transcribe_multilingual(pcm, "te")) == "నమస్కారం"
    assert calls == ["te"]
