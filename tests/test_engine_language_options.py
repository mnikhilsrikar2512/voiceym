"""Language metadata must use the same declarations as synthesis guards."""
import pytest
@pytest.fixture
def tts():
    from services import tts_backend
    return tts_backend

@pytest.mark.parametrize('engine,allowed,rejected', [
    ('kittentts', 'english', 'polish'),
    ('kokoro', 'hindi', 'polish'),
])
def test_finite_language_options_without_loading_models(engine, allowed, rejected, monkeypatch, tts):
    options = tts.language_options(engine)
    assert allowed in options
    assert rejected not in options


def test_open_ended_engine_keeps_all_language_options(tts):
    from omnivoice.utils.lang_map import LANG_NAME_TO_ID
    assert tts.language_options('omnivoice') == sorted(LANG_NAME_TO_ID)


def test_unknown_engine_does_not_break_inventory(tts):
    assert tts.language_options('third-party-unknown') is None


def test_renderer_code_catalog_matches_backend_vocabulary():
    import json
    from pathlib import Path
    from omnivoice.utils.lang_map import LANG_NAME_TO_ID
    path = Path(__file__).resolve().parents[1] / 'electron/src/shared/language-codes.json'
    assert json.loads(path.read_text(encoding='utf-8')) == LANG_NAME_TO_ID


def test_flag_search_regions_match_the_rendered_flags():
    import json
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'electron/src/shared'
    source = (root / 'components/LanguageFlag.jsx').read_text(encoding='utf-8')
    body = source.split('export const LANGUAGE_FLAGS = {')[1].split('};')[0]
    expected = {key.strip("'\""): region.replace('_', '-')
                for key, region in re.findall(r"\s+([\w'\"-]+): ([A-Z_]+),", body)}
    assert json.loads((root / 'language-regions.json').read_text(encoding='utf-8')) == expected


def test_active_engine_inventory_carries_language_choices(monkeypatch, tts):
    from api.routers import engines
    monkeypatch.setattr(tts, 'active_backend_id', lambda: 'kittentts')
    monkeypatch.setattr(tts, 'list_backends', lambda: [
        {'id': 'kittentts', 'available': True},
        {'id': 'omnivoice', 'available': False},
    ])
    response = engines._family_payload('tts', tts)
    assert 'english' in response['backends'][0]['supported_language_names']
    assert 'polish' not in response['backends'][0]['supported_language_names']
    assert 'supported_language_names' not in response['backends'][1]


@pytest.mark.parametrize('engine', ['omnivoice-subprocess', 'voxcpm2'])
def test_metadata_releases_temporary_sidecar_exit_handlers(monkeypatch, engine, tts):
    import atexit
    callbacks = []
    monkeypatch.setattr(atexit, 'register', lambda callback: callbacks.append(callback))
    monkeypatch.setattr(atexit, 'unregister', lambda callback: callbacks.remove(callback))
    for _ in range(3):
        tts.language_options(engine)
    assert callbacks == []

