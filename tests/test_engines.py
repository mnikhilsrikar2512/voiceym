"""Phase 3 — TTS / ASR / LLM adapter registries."""
import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import sys
import types

import pytest
from services import tts_backend, asr_backend, llm_backend


# ── TTS ─────────────────────────────────────────────────────────────────────


def test_tts_registry_lists_all_backends():
    rows = tts_backend.list_backends()
    ids = {r["id"] for r in rows}
    assert {"kokoro", "kittentts", "voxcpm2", "chatterbox", "indic-parler", "indic-tts"} == ids
    for r in rows:
        assert set(r) >= {"id", "display_name", "available", "reason"}
        evidence = r["execution_evidence"]
        assert evidence["implementation_variant"]
        assert evidence["declared_device_families"] == r["gpu_compat"]
        assert evidence["evidence_state"] in {
            "loaded", "not_loaded", "subprocess_loaded_provider_unreported"
        }
        assert evidence["runtime_versions"]["python"]


def test_tts_voxcpm2_unavailable_message_is_actionable():
    ok, msg = tts_backend.VoxCPM2Backend.is_available()
    # On most CI boxes voxcpm isn't installed; message must tell the user how
    # — including the >=2.0.3 version floor (Apple-Silicon audio quality).
    if not ok:
        assert 'pip install "voxcpm>=2.0.3"' in msg


def test_tts_voxcpm2_upgrade_hint_reaches_list_backends(monkeypatch):
    """The reported instance of the dropped-ok-message class: an old-but-working
    voxcpm install reports available=True, and its ">=2.0.3" upgrade advice must
    reach the UI via the additive `hint` field instead of being dropped."""
    monkeypatch.setitem(sys.modules, "voxcpm", types.ModuleType("voxcpm"))
    monkeypatch.setattr(tts_backend, "_voxcpm_installed_version", lambda: "2.0.1")
    entry = next(e for e in tts_backend.list_backends() if e["id"] == "voxcpm2")
    assert entry["available"] is True
    assert entry["reason"] is None
    assert entry["hint"] is not None
    assert "2.0.3" in entry["hint"]
    assert 'pip install --upgrade "voxcpm>=2.0.3"' in entry["hint"]


def test_tts_active_backend_env_override(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "voxcpm2")
    assert tts_backend.active_backend_id() == "voxcpm2"
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    # Reset prefs in case an earlier test persisted a choice.
    from core import prefs as _prefs
    _prefs.set_("tts_backend", "omnivoice")
    assert tts_backend.active_backend_id() == "omnivoice"


# ── #919: sherpa-onnx gates on OMNIVOICE_SHERPA_MODEL ────────────────────────
# sherpa-onnx ships no bundled model, so with the package installed but no model
# dir configured it must report unavailable-with-a-reason — not "ready" and then
# a generate-time config error the OOM catch-all mislabeled. A fake module makes
# these deterministic whether or not sherpa-onnx is installed on CI.


def test_tts_active_backend_prefs_fallback(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    _prefs.set_("tts_backend", "moss-tts-nano")
    assert tts_backend.active_backend_id() == "moss-tts-nano"
    # Env var must beat prefs.
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "voxcpm2")
    assert tts_backend.active_backend_id() == "voxcpm2"


def test_tts_sample_rate_per_backend():
    assert tts_backend.OmniVoiceBackend().sample_rate == 24000
    assert tts_backend.VoxCPM2Backend().sample_rate == 48000


def test_tts_unknown_backend_raises():
    with pytest.raises(ValueError):
        tts_backend.get_backend_class("not-a-real-one")


# ── #981 — MLX-Audio curated-model selection ─────────────────────────────
#
# MLXAudioBackend.__init__ used to resolve its active model ONLY from
# OMNIVOICE_MLX_AUDIO_MODEL, invisible to Settings and unchangeable without
# restarting the process with an env var set. It must now mirror
# active_backend_id()'s env > prefs > default resolution.


# ── ASR ─────────────────────────────────────────────────────────────────────


def test_asr_registry_lists_backends():
    rows = asr_backend.list_backends()
    ids = {r["id"] for r in rows}
    assert {"mlx-whisper", "pytorch-whisper"}.issubset(ids)
    required = {
        "actual_execution_provider",
        "actual_execution_device",
        "cpu_fallback_reason",
        "cpu_fallback_stage",
        "runtime_versions",
    }
    assert all(required.issubset(row["execution_evidence"]) for row in rows)


def test_asr_auto_detects():
    bid = asr_backend.active_backend_id()
    # WhisperX is now the default cross-platform pick (better wav2vec2 word
    # alignment for lip-sync); mlx / pytorch / faster-whisper are fallbacks.
    assert bid in {"whisperx", "faster-whisper", "mlx-whisper", "pytorch-whisper"}


def test_asr_env_override(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "pytorch-whisper")
    assert asr_backend.active_backend_id() == "pytorch-whisper"


# ── ASR selection resolution (Model Catalogue ASR picker) ────────────────
# Same env > prefs > auto-detect contract as TTS. The env var MUST keep
# winning so existing `OMNIVOICE_ASR_BACKEND` pins don't change behavior now
# that the Settings picker writes the prefs key.


def test_asr_active_backend_prefs_fallback(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    _prefs.set_("asr_backend", "moonshine")
    assert asr_backend.active_backend_id() == "moonshine"
    # Env var must beat prefs.
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "pytorch-whisper")
    assert asr_backend.active_backend_id() == "pytorch-whisper"


def test_asr_auto_detects_when_no_env_no_prefs(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    assert asr_backend.active_backend_id() in {
        "whisperx", "faster-whisper", "mlx-whisper", "pytorch-whisper",
    }


def test_get_active_asr_backend_follows_prefs_switch_without_restart(monkeypatch, tmp_path):
    """#981 class (fixed on the TTS side): a Settings pick must take effect on
    the next transcribe, not after an app restart. get_active_asr_backend()
    re-resolves the id per call, so a prefs write switches immediately."""
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    _prefs.set_("asr_backend", "pytorch-whisper")
    assert isinstance(
        asr_backend.get_active_asr_backend(), asr_backend.PyTorchWhisperBackend)
    _prefs.set_("asr_backend", "moonshine")
    assert isinstance(
        asr_backend.get_active_asr_backend(), asr_backend.MoonshineASRBackend)


def test_asr_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "not-a-real-asr")
    with pytest.raises(ValueError):
        asr_backend.get_active_asr_backend()


# ── LLM ─────────────────────────────────────────────────────────────────────


def test_llm_registry_includes_off():
    rows = llm_backend.list_backends()
    ids = {r["id"] for r in rows}
    assert ids == {"openai-compat", "off"}


def test_llm_off_chat_raises_actionable(monkeypatch):
    # Force selection to Off regardless of env.
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    be = llm_backend.get_active_llm_backend()
    assert isinstance(be, llm_backend.OffBackend)
    with pytest.raises(RuntimeError) as ei:
        be.chat(system="x", user="y")
    # Error message tells the user what env vars unlock Cinematic translate.
    assert "TRANSLATE_BASE_URL" in str(ei.value)


def test_llm_auto_selects_off_when_nothing_configured(clean_llm_env):
    # clean_llm_env (conftest) clears the FULL provider env surface — a
    # hand-picked 4-var list left e.g. LLM_DEFAULT_PROVIDER / GROQ_API_KEY
    # standing when an earlier test imported `main` (which dotenv-loads the
    # developer's .env into os.environ), reading as 'configured' (#878).
    assert llm_backend.active_backend_id() == "off"


def test_llm_auto_selects_openai_compat_when_configured(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_LLM_BACKEND", raising=False)
    monkeypatch.setenv("TRANSLATE_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TRANSLATE_API_KEY", "local")
    monkeypatch.setenv("TRANSLATE_MODEL", "fixture-model")
    # is_available itself also needs the openai pkg to import — that's fine;
    # translator.py already depends on it in this repo.
    try:
        import openai  # noqa: F401
    except ImportError:
        pytest.skip("openai package not available in this environment")
    assert llm_backend.active_backend_id() == "openai-compat"


# ── HF Hub closed-client recovery (#880) ────────────────────────────────────
#
# huggingface_hub ≥1.x shares one global httpx client; if it gets closed
# mid-lifecycle, an engine's first-use model download inside the generate
# path dies with "Cannot send a request, as the client has been closed".
# The load must retry exactly once with a fresh client — and must NOT retry
# unrelated failures.


def test_hf_retry_recovers_from_closed_client_once():
    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return "model"

    assert tts_backend._retry_once_with_fresh_hf_client(loader, what="test") == "model"
    assert len(calls) == 2


def test_hf_retry_matches_wrapped_closed_client_error():
    # An engine can wrap the httpx error — detection walks the chain.
    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            try:
                raise RuntimeError("Cannot send a request, as the client has been closed.")
            except RuntimeError as inner:
                raise RuntimeError("KittenTTS init failed") from inner
        return "model"

    assert tts_backend._retry_once_with_fresh_hf_client(loader, what="test") == "model"
    assert len(calls) == 2


def test_hf_retry_does_not_retry_unrelated_errors():
    calls = []

    def loader():
        calls.append(1)
        raise ValueError("bad checkpoint id")

    with pytest.raises(ValueError):
        tts_backend._retry_once_with_fresh_hf_client(loader, what="test")
    assert len(calls) == 1


def test_hf_retry_is_single_shot():
    # A second closed-client failure propagates (the generation classifier
    # then labels it a network problem) — no infinite retry loop.
    calls = []

    def loader():
        calls.append(1)
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    with pytest.raises(RuntimeError):
        tts_backend._retry_once_with_fresh_hf_client(loader, what="test")
    assert len(calls) == 2


# ── #977: MLX-Audio Kokoro language-code resolution ─────────────────────────
# Kokoro's own vendored pipeline (mlx_audio.tts.models.kokoro.pipeline)
# hard-asserts `lang_code` against a fixed single-letter table. The old code
# blindly truncated a full language name — "Dutch"[:2].lower() == "du" — into
# that assert, crashing with an unreadable `(lang_code, LANG_CODES)` repr
# instead of a clean error. The resolution tests need the real mlx-audio
# package (Apple-Silicon-only) since they validate against ITS installed
# table, never a hardcoded guess; they skip cleanly where mlx-audio isn't
# installed (every non-macOS-ARM CI runner).


# The label derivation takes the tables as arguments, so it runs on every
# platform — unlike the resolution tests above, which need the real
# Apple-Silicon-only package. The bug it guards is invisible on the runners
# that skip.


class _PickedModelBackend(tts_backend.TTSBackend):
    """An engine whose model is a construction-time preference."""

    id = "picked-model"
    sample_rate = 24000

    def __init__(self):
        self._built = os.environ.get("VOICEYM_TEST_PICKED_MODEL")

    @classmethod
    def configured_identity(cls):
        return os.environ.get("VOICEYM_TEST_PICKED_MODEL")

    def built_identity(self):
        return self._built

    @classmethod
    def is_available(cls):
        return True, ""

    def supported_languages(self):
        return ["en"]

    def generate(self, *args, **kwargs):  # pragma: no cover - never synthesizes
        raise NotImplementedError


def test_cached_engine_follows_a_model_switch(monkeypatch):
    # Selecting another model must not keep synthesizing with the model the
    # cached instance was built for.
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    monkeypatch.setenv("VOICEYM_TEST_PICKED_MODEL", "first")
    first = tts_backend.get_engine_instance(_PickedModelBackend)
    unloaded = []
    first.unload = lambda: unloaded.append(True)
    assert tts_backend.get_engine_instance(_PickedModelBackend) is first
    monkeypatch.setenv("VOICEYM_TEST_PICKED_MODEL", "second")
    second = tts_backend.get_engine_instance(_PickedModelBackend)
    assert second is not first
    assert second.built_identity() == "second"
    assert unloaded == [True]


def test_model_switch_never_unloads_an_engine_mid_job(monkeypatch):
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    monkeypatch.setenv("VOICEYM_TEST_PICKED_MODEL", "first")
    first = tts_backend.get_engine_instance(_PickedModelBackend)
    unloaded = []
    first.unload = lambda: unloaded.append(True)
    with tts_backend.engine_in_use(first):
        monkeypatch.setenv("VOICEYM_TEST_PICKED_MODEL", "second")
        second = tts_backend.get_engine_instance(_PickedModelBackend)
        assert not unloaded
    assert unloaded == [True]
    assert second is not first



def test_every_listed_tts_engine_is_free_for_commercial_use(monkeypatch):
    """Only commercial-use engines are offered (owner rule, 2026-10-24): every
    model a listed engine loads must be in the commercial licence category,
    which is exactly what needs no licence acceptance."""
    from services import model_acceptance

    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    monkeypatch.setattr("core.prefs.get", lambda key, default=None: default)
    for row in tts_backend.list_backends():
        for repo in model_acceptance.repos_for_engine(row["id"]):
            assert model_acceptance.status(repo)["required"] is False, (row["id"], repo)
    assert model_acceptance.status("k2-fsa/OmniVoice")["required"] is True
