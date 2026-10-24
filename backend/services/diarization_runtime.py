"""Persisted selection and installed-only resolution for diarisation runtimes.

pyannote (MIT) is the only diarisation runtime. A saved choice from an older
release (the non-commercial Sortformer runtime) resolves to pyannote.
"""
from __future__ import annotations

from core import prefs

PYANNOTE = "pyannote"


def selected_backend() -> str:
    # Read the preference so an environment pin keeps its documented meaning,
    # but every value resolves to the one runtime that ships.
    prefs.resolve("diarization_backend", env="OMNIVOICE_DIARIZATION_BACKEND", default=PYANNOTE)
    return PYANNOTE


PYANNOTE_REPOS = (
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
)


def selected_model_repos() -> list[str]:
    """Registry models the selected diarisation runtime loads."""
    return list(PYANNOTE_REPOS)


def ensure_selected_accepted() -> None:
    """Raise ``ModelLicenceNotAccepted`` before an unaccepted diarisation model runs."""
    from services.model_acceptance import ensure_accepted

    ensure_accepted(selected_model_repos())


def select_backend(backend: str) -> None:
    if backend != PYANNOTE:
        raise ValueError("Unknown diarisation engine")
    prefs.set_("diarization_backend", backend)


def installed_backends() -> set[str]:
    """Return complete local runtimes without loading weights or downloading."""
    from api.routers.setup.models import KNOWN_MODELS, cache_is_complete, is_cached

    repo_id = "pyannote/speaker-diarization-3.1"
    spec = next(model for model in KNOWN_MODELS if model["repo_id"] == repo_id)
    return {PYANNOTE} if is_cached(repo_id) and cache_is_complete(spec) else set()
