"""The secret-scan allowlist must remain exact and value-scoped."""
import base64
import hashlib
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
# Upstream VoiceStudio's public PostHog token. Voiceym no longer ships it, but
# it stays in git history, so the full-history scan still needs exactly one
# anchored entry for it (identified by its SHA-256, never the literal).
_UPSTREAM_POSTHOG_SHA256 = "20b808bcff907f1afac896c5b835a04415a4b914c4314477314397f0f3f6fc38"
_POSTHOG_ENTRIES = [
    regex for regex in tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))["allowlist"]["regexes"]
    if regex.startswith("^phc_")
]
assert [hashlib.sha256(r[1:-1].encode()).hexdigest() for r in _POSTHOG_ENTRIES] == [_UPSTREAM_POSTHOG_SHA256]
EXPECTED_EXACT_REGEXES = {
    _POSTHOG_ENTRIES[0],
    "^528e871c2a26c4f0f7773b9754e2e1acae20899d$",
    "^0613663d0d48ea86ba8cb3d7a44f0f65dc596a2a$",
    "^hf_abcdefghijklmnopqrstuvwxyz01234567890abcd$",
    "^hf_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF$",
    "^hf_QWERTYUIOPasdfghjklZXCVBNM0123456789xyzAB$",
    "^max_length=400$",
    "^Ed25519PrivateKey$",
    r"^omnivoice\.dubSplit\.v1$",
}


def test_gitleaks_allowlist_contains_only_reviewed_exact_values():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    allowlist = config["allowlist"]

    assert set(allowlist) == {"description", "regexes"}
    assert set(allowlist["regexes"]) == EXPECTED_EXACT_REGEXES
    assert all(
        regex.startswith("^") and regex.endswith("$")
        for regex in allowlist["regexes"]
    )
    assert "rules" not in config


def test_dub_storage_key_allowlist_does_not_hide_similar_credentials():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    regexes = config["allowlist"]["regexes"]
    assert any(re.fullmatch(regex, "omnivoice.dubSplit.v1") for regex in regexes)
    for value in (
        "omnivoiceXdubSplitXv1",
        "omnivoice.dubSplit.v1-secret",
        "secret-omnivoice.dubSplit.v1",
    ):
        assert not any(re.fullmatch(regex, value) for regex in regexes)


# SHA-256 of each retired PostHog project token that history still holds (the
# value itself may only appear in the two canonical analytics files).
_RETIRED_TOKEN_SHA256 = {"cf19c9566b83f7375f591f5fbf776b9c344025805d12c16bd21333f70a708f72"}
_FINGERPRINT = re.compile(r"^([0-9a-f]{40}):([^:]+):([a-z0-9-]+):([0-9]+)$")


def _ignored_fingerprints():
    text = (ROOT / ".gitleaksignore").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


# The RFC 6455 section 1.3 sample Sec-WebSocket-Key, built here so this file
# never holds the literal gitleaks would flag.
_RFC6455_SAMPLE_KEY = base64.b64encode(b"the sample nonce").decode()


def test_gitleaks_ignores_only_commit_pinned_posthog_findings():
    """An ignore entry names one finding in one commit, never a path or a rule."""
    import subprocess

    entries = _ignored_fingerprints()
    assert entries
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip() == "true"
    for entry in entries:
        match = _FINGERPRINT.fullmatch(entry)
        assert match, f"not a commit-pinned fingerprint: {entry}"
        commit, path, _rule, line = match.groups()
        if shallow:
            continue
        shown = subprocess.run(
            ["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True, text=True,
        )
        assert shown.returncode == 0, f"cannot read the pinned commit and path: {entry}"
        source_line = shown.stdout.splitlines()[int(line) - 1]
        # The RFC 6455 section 1.3 sample Sec-WebSocket-Key is a published
        # protocol example, not a credential; nothing else but it may match.
        if _RFC6455_SAMPLE_KEY in source_line:
            # Either the header in the fake backend test, or this file's own
            # constant before it stopped spelling out the literal.
            assert re.search(
                r"(?:'Sec-WebSocket-Key':\s*|_RFC6455_SAMPLE_KEY = )['\"]"
                + re.escape(_RFC6455_SAMPLE_KEY) + r"['\"]",
                source_line,
            ), entry
            continue
        # Otherwise only the retired public PostHog project token may be skipped.
        token = re.search(r"phc_[A-Za-z0-9]{20,}", source_line)
        assert token, entry
        assert hashlib.sha256(token.group(0).encode()).hexdigest() in _RETIRED_TOKEN_SHA256, entry
