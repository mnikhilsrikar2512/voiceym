"""Languages Indic-TTS speaks, and which one a piece of text is in.

Standard library only: the sidecar (engine venv) and the app both import it.
"""
from __future__ import annotations

from typing import Optional

#: AI4Bharat's v1 release (MIT): one FastPitch + HiFi-GAN pair per language,
#: each trained on a male and a female speaker. Hindi is left out on purpose:
#: Kokoro already speaks it, faster.
RELEASE_URL = "https://github.com/AI4Bharat/Indic-TTS/releases/download/v1-checkpoints-release"

#: code -> (name, sha256 of the release zip, size in bytes)
LANGUAGES: dict[str, tuple[str, str, int]] = {
    "as": ("Assamese", "c2f4c1de3758fd5d723c724d4cbe067c8be234577e1d7c48a8bf4dae5e4b908d", 1512387643),
    "bn": ("Bengali", "4250053d69a15c75ad3e48a1e2d2ef025f75a5b22e9fde2e0a45225b6899fb6f", 1512955815),
    "brx": ("Bodo", "12566b9341d5e4e1a65f5d58b237df19a44c295f28524e6b3c8331ba8a37ae78", 1522282712),
    "gu": ("Gujarati", "7cb5bfa35e14f93469c3f56afa9225d0494615a961868b2966ac4f04f8f7e8d0", 1518484594),
    "kn": ("Kannada", "1ba360afa417f4346ddb8573ac74032c93dde83f4a5b90b6ef0d7c7976268216", 1519176042),
    "ml": ("Malayalam", "a3af3d8ec2f449da6e17f0f6cce6c1ba2ab6dc14ac399e9853d7833fe170a2cc", 1514083272),
    "mni": ("Manipuri", "f689abf7d7398b99b40e589350beae6ceee62bdde880eb8c217b2d076e6683e5", 1518195233),
    "mr": ("Marathi", "d4457b2b9d2604396ab214d6ef8b3d4689f3bfd1312814ca295f44af5def90a0", 1514815493),
    "or": ("Odia", "eff767d91415fde8b9c24a01aa37076c56d468a97efca42e5c9dc78f44c9500b", 1516409500),
    "pa": ("Punjabi", "129660c4360db13c1511e5382753bf68001310e02a57787f1fcf7246b9708ad9", 1532171142),
    "ta": ("Tamil", "9c443f2304df959db3e8ae308a6380cefb24084901551a9e77188a992cf578e5", 1520799925),
    "te": ("Telugu", "c568b55c5e3317c18fc942f46703ff0664beef75c23fcfa059a8396574b0cf23", 1513806634),
}

_NAMES = {name.lower(): code for code, (name, _, _) in LANGUAGES.items()}
_NAMES.update({"bangla": "bn", "oriya": "or", "panjabi": "pa"})

#: Script ranges -> language. Devanagari is ambiguous (Hindi, Marathi, Bodo,
#: Nepali…), so it only counts when the session names the language.
_SCRIPTS = (
    (0x0980, 0x09FF, "bn"), (0x0A00, 0x0A7F, "pa"), (0x0A80, 0x0AFF, "gu"),
    (0x0B00, 0x0B7F, "or"), (0x0B80, 0x0BFF, "ta"), (0x0C00, 0x0C7F, "te"),
    (0x0C80, 0x0CFF, "kn"), (0x0D00, 0x0D7F, "ml"), (0xABC0, 0xABFF, "mni"),
    (0x0900, 0x097F, "deva"),
)


def named_language(language: object) -> Optional[str]:
    """``"Tamil"``, ``"ta"`` or ``"ta-IN"`` -> ``"ta"``; None when not one of ours."""
    if not isinstance(language, str):
        return None
    value = language.strip().lower()
    if value in _NAMES:
        return _NAMES[value]
    head = value.replace("_", "-").split("-", 1)[0]
    return head if head in LANGUAGES else None


def detect_language(text: str, language: object = None) -> Optional[str]:
    """The language to speak *text* in: the session's named language when it
    is one of ours (Assamese and Bengali share a script, so do Marathi and
    Hindi), else the script the text is mostly written in. None when Indic-TTS
    can't say it."""
    named = named_language(language)
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for lo, hi, code in _SCRIPTS:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    if not counts:
        return None
    script = max(counts, key=counts.get)
    if named and (script == _script_of(named)):
        return named
    if script == "deva" or script not in LANGUAGES:
        return None
    return script


def _script_of(code: str) -> str:
    if code in ("mr", "brx"):
        return "deva"
    if code == "as":
        return "bn"
    return code
