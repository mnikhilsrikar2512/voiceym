"""#2201: the cloning gate's message must match what the engine can do.

An engine whose ``supports_cloning`` is a plain flag has no model to switch
to, so the engine-switch message is the right one.
"""
import pytest


def _tts_backend():
    """Resolve the live module after other suites replace backend modules."""
    from services import tts_backend

    return tts_backend


def _detail(*args):
    """Resolved lazily so the behavioural tests below fail on their assertions
    rather than on a missing symbol against a tree without the fix."""
    from services import tts_backend

    return tts_backend.cloning_unavailable_detail(*args)


# ── nothing else moves ──────────────────────────────────────────────────────


def test_a_fixed_non_cloning_engine_keeps_the_original_message():
    """KittenTTS declares a plain `supports_cloning = False` — there is no
    model to switch to, so the engine-switch message is the right one."""
    cls = _tts_backend().KittenTTSBackend
    backend = cls.__new__(cls)
    detail = _detail("kittentts", backend, "dubbing")

    assert "doesn't support voice cloning" in detail
    assert "Model Catalogue" in detail
    # No model advice, because there is no model choice to give.
    assert "this engine's model" not in detail


# ── the invariant that keeps this fixed ─────────────────────────────────────


def test_a_fixed_flag_engine_declares_no_cloning_models():
    """`cloning_model_keys` is only meaningful where the flag is model-derived;
    a plain bool with model keys would be a contradiction."""
    from services import tts_backend

    for engine_id, cls in tts_backend._REGISTRY.items():
        flag = getattr(cls, "supports_cloning", True)
        if isinstance(flag, bool) and getattr(cls, "cloning_model_keys", ()):
            pytest.fail(
                f"{engine_id}: declares cloning_model_keys but supports_cloning "
                f"is a fixed {flag}"
            )
