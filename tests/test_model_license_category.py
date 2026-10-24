"""Declared-licence categories: most restrictive part wins, unmapped is unknown."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services import model_licenses as ml


@pytest.mark.parametrize(("license", "category"), [
    ("mit", "commercial"),
    ("Apache-2.0", "commercial"),
    ("cc-by-4.0", "commercial"),
    ("openrail", "conditions"),
    ("CC-BY-SA-4.0", "conditions"),
    ("Llama-3.2", "conditions"),
    ("CC-BY-NC-4.0", "noncommercial"),
    ("CC-BY-NC (version unspecified); Higgs Audio 2; Meta Llama 3", "noncommercial"),
    ("CC-BY-NC-SA-4.0 AND Llama-3.2", "noncommercial"),
    ("mit AND openrail", "conditions"),
    ("apache-2.0; NOASSERTION", "unknown"),
    ("NOASSERTION", "unknown"),
    ("other", "unknown"),
    ("mit-but-typo", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_licence_strings_map_to_most_restrictive_category(license, category):
    assert ml.license_category({"license": license}) == category


@pytest.mark.parametrize("status", ["restricted", "noncommercial_current_upstream_terms"])
def test_restrictive_review_status_overrides_permissive_licence(status):
    assert ml.license_category({"license": "mit", "commercial_inference": status}) == "noncommercial"


def _registry_with_variants(*variants):
    """The real registry with the Kokoro record carrying the given variants."""
    import copy

    registry = copy.deepcopy(ml.load_registry())
    row = next(r for r in registry["models"] if r["id"] == "onnx-community/Kokoro-82M-v1.0-ONNX")
    row["variants"] = [dict(v) for v in variants]
    return registry


def test_restricted_variant_makes_the_whole_repo_noncommercial():
    registry = _registry_with_variants(
        {"id": "kokoro:restricted", "commercial_inference": "noncommercial_current_upstream_terms"})
    info = ml.disclosure("onnx-community/Kokoro-82M-v1.0-ONNX", registry)
    assert info["license_category"] == "noncommercial"
    assert {v["license_category"] for v in info["variants"]} == {"noncommercial"}


def test_variant_without_own_terms_inherits_repo_category():
    registry = _registry_with_variants({"id": "kokoro:plain"})
    info = ml.disclosure("onnx-community/Kokoro-82M-v1.0-ONNX", registry)
    assert info["variants"]
    assert {v["license_category"] for v in info["variants"]} == {info["license_category"]}


def test_missing_record_and_invalid_registry_are_unknown():
    assert ml.disclosure("nobody/missing", ml.load_registry())["license_category"] == "unknown"


def test_every_registry_licence_token_is_deliberately_mapped():
    """A new licence id must be categorised on purpose, not fall through."""
    registry = ml.load_registry()
    values = []
    for row in registry["models"]:
        values.append(row.get("license"))
        values += [(v.get("observations") or {}).get("license") for v in row.get("variants", [])]
    unmapped = {t for v in values for t in ml.license_tokens(v) if ml.license_token_category(t) is None}
    assert unmapped == set(), f"map these in backend/services/model_licenses.py: {sorted(unmapped)}"
