"""Whisper fine-tuned for one Indian language, for Talk's hearing.

General Whisper (large-v3-turbo) hears Telugu poorly and Hindi only fairly.
IIT Madras's SPRING Lab fine-tuned Whisper on each language (Apache-2.0, not
gated, no account); the small builds are far more accurate on their language
and need about half the memory of turbo. Talk uses one when the conversation's
language has one installed, and falls back to the multilingual tier otherwise.

Only one is kept loaded, so switching language frees the previous model.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("omnivoice.asr")

#: language code -> Hugging Face repo (Apache-2.0, SPRING Lab, IIT Madras)
MODELS: dict[str, str] = {
    "te": "vasista22/whisper-telugu-small",
    "hi": "vasista22/whisper-hindi-small",
}

#: The files the pipeline needs; the Flax copy and training args are skipped.
ALLOW_PATTERNS = (
    "config.json", "generation_config.json", "preprocessor_config.json",
    "pytorch_model.bin", "added_tokens.json", "merges.txt", "normalizer.json",
    "special_tokens_map.json", "tokenizer_config.json", "vocab.json",
)

_lock = threading.Lock()
_loaded: tuple[str, object] | None = None


def _revision(repo: str) -> str:
    from services.hf_revisions import revision_for

    return revision_for(repo)


def installed(code: str) -> bool:
    """The model for ``code`` is fully on disk (never downloads)."""
    repo = MODELS.get(code)
    if repo is None:
        return False
    try:
        from huggingface_hub import try_to_load_from_cache

        rev = _revision(repo)
        return all(
            isinstance(try_to_load_from_cache(repo, name, revision=rev), str)
            for name in ("config.json", "pytorch_model.bin")
        )
    except Exception:  # noqa: BLE001 — an unreadable cache counts as not installed
        return False


def _device_and_dtype():
    import torch

    if torch.cuda.is_available():
        return "cuda", torch.float16
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps", torch.float16
    return "cpu", torch.float32


def _pipeline(code: str):
    global _loaded
    if _loaded is not None and _loaded[0] == code:
        return _loaded[1]
    from huggingface_hub import snapshot_download
    from transformers import pipeline

    repo = MODELS[code]
    path = snapshot_download(repo, revision=_revision(repo), allow_patterns=list(ALLOW_PATTERNS),
                             local_files_only=True)
    _loaded = None  # free the previous language before loading the next
    device, dtype = _device_and_dtype()
    logger.info("Loading %s for Talk on %s", repo, device)
    pipe = pipeline("automatic-speech-recognition", model=path, torch_dtype=dtype, device=device)
    _loaded = (code, pipe)
    return pipe


def transcribe(audio_path: str, code: str) -> str:
    """Text of ``audio_path`` in language ``code``; blocking, run it on the GPU pool."""
    with _lock:
        pipe = _pipeline(code)
        result = pipe(audio_path, generate_kwargs={"language": code, "task": "transcribe"})
    return str((result or {}).get("text", "")).strip()


def unload() -> None:
    global _loaded
    with _lock:
        _loaded = None
