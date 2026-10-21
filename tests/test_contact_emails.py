"""Voiceym publishes exactly three contact addresses (owner-set 2026-10-02).

    hi@voiceym.example        general, business, licensing, collaboration
    partner@voiceym.example   brand and company partnerships
    security@voiceym.example  vulnerability reports

Any other real address in a shipped file (docs, UI, locales, package metadata,
legal text) is a stale or personal contact. Tests and fixtures may use
reserved example domains (RFC 2606 / RFC 6761) or fake addresses, and docs may
show GitHub no-reply commit addresses.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CONTACTS = {"hi@voiceym.example", "partner@voiceym.example", "security@voiceym.example"}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_RESERVED_DOMAIN = re.compile(r"(^|\.)(example\.(com|net|org)|example|test|invalid|localhost|local)$", re.I)
# GitHub no-reply commit addresses are identities, not contact addresses.
_NOREPLY_DOMAIN = "users.noreply.github.com"
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|fixtures)/|\.test\.[a-z]+$|_test\.py$")


def _tracked_text_files():
    names = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout.split("\0")
    for name in filter(None, names):
        try:
            yield name, (_REPO / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


def test_only_project_contact_addresses_are_published():
    stray = []
    for name, text in _tracked_text_files():
        for address in _EMAIL.findall(text):
            domain = address.rsplit("@", 1)[1]
            # CHANGELOG.md keeps upstream VoiceStudio's history verbatim.
            if (address.lower() in _CONTACTS or _RESERVED_DOMAIN.search(domain) or domain.lower() == _NOREPLY_DOMAIN
                    or _TEST_PATH.search(name) or name == "CHANGELOG.md"):
                continue
            stray.append(f"{name}: {address}")
    assert not stray, "Use hi@, partner@ or security@voiceym.example:\n" + "\n".join(stray)
