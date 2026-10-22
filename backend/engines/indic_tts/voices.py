"""Download Indic-TTS voices, one language at a time, with no account.

Each language is a ~1.5 GB zip on AI4Bharat's GitHub release. The download is
checked against the SHA-256 recorded in ``indic_tts_languages``, and only the
six files inference needs are kept, under ``<install>/voices/<code>/``. A
download runs once in the background; callers ask for it and check later,
so a live conversation never waits on it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .indic_tts_languages import LANGUAGES, RELEASE_URL

logger = logging.getLogger("omnivoice.tts")

#: Files a voice needs, relative to the language folder in the zip.
VOICE_FILES = (
    "fastpitch/best_model.pth", "fastpitch/config.json", "fastpitch/speakers.pth",
    "hifigan/best_model.pth", "hifigan/config.json",
)
_COMPLETE = ".voiceym-complete"
_CHUNK = 1024 * 1024

_lock = threading.Lock()
_running: dict[str, threading.Thread] = {}
_failed: dict[str, str] = {}


def voices_root() -> Optional[Path]:
    install = os.environ.get("OMNIVOICE_INDIC_TTS_DIR")
    return Path(install) / "voices" if install else None


def voice_installed(code: str) -> bool:
    root = voices_root()
    return bool(root and code in LANGUAGES and (root / code / _COMPLETE).is_file())


def downloading(code: str) -> bool:
    with _lock:
        thread = _running.get(code)
        return bool(thread and thread.is_alive())


def last_error(code: str) -> str:
    with _lock:
        return _failed.get(code, "")


def start_download(code: str) -> bool:
    """Start downloading *code*'s voice in the background unless it is already
    installed or downloading. True when a download is (now) in progress."""
    if code not in LANGUAGES or voice_installed(code) or voices_root() is None:
        return False
    with _lock:
        thread = _running.get(code)
        if thread and thread.is_alive():
            return True
        _failed.pop(code, None)
        thread = threading.Thread(target=_download_logged, args=(code,), daemon=True,
                                  name=f"indic-tts-voice-{code}")
        _running[code] = thread
        thread.start()
    return True


def _download_logged(code: str) -> None:
    try:
        download_voice(code)
    except Exception as exc:  # noqa: BLE001 -- reported through last_error()
        logger.warning("Indic-TTS %s voice download failed: %s", code, exc)
        with _lock:
            _failed[code] = str(exc)


def download_voice(code: str, *, opener=urllib.request.urlopen) -> Path:
    """Download, verify and unpack one language's voice. Blocking."""
    name, sha256, size = LANGUAGES[code]
    root = voices_root()
    if root is None:
        raise RuntimeError("Indic-TTS isn't installed.")
    target = root / code
    if voice_installed(code):
        return target
    root.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading the Indic-TTS %s voice (%.1f GB)", name, size / 1e9)
    with tempfile.TemporaryDirectory(dir=root) as work:
        archive = Path(work) / f"{code}.zip"
        digest = hashlib.sha256()
        with opener(f"{RELEASE_URL}/{code}.zip", timeout=60) as response, open(archive, "wb") as out:
            while chunk := response.read(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256:
            raise RuntimeError(f"The {name} voice download was corrupted; try again.")
        staging = Path(work) / code
        with zipfile.ZipFile(archive) as bundle:
            for rel in VOICE_FILES:
                member = f"{code}/{rel}"
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, _CHUNK)
        archive.unlink()
        (staging / _COMPLETE).write_text(sha256 + "\n", encoding="ascii")
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
    logger.info("Indic-TTS %s voice ready", name)
    return target


__all__ = [
    "VOICE_FILES", "download_voice", "downloading", "last_error",
    "start_download", "voice_installed", "voices_root",
]
