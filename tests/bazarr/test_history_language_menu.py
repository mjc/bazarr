import pytest

from api.system import languages
from app.database import TableHistory, TableHistoryMovie
from languages import get_languages
from languages.registry import LanguageRegistry


def test_history_language_menu_uses_registry_and_keeps_nullable_names(
    memory_session, api_client, api_key, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(languages, 'database', memory_session)
    registry = LanguageRegistry([
        {'code2': 'en', 'code3': 'eng', 'code3b': None, 'name': 'English'},
        {'code2': 'fr', 'code3': 'fra', 'code3b': 'fre', 'name': 'French'},
    ])
    monkeypatch.setattr(get_languages, '_language_registry', registry)
    for table in (TableHistory, TableHistoryMovie):
        memory_session.add_all([table(action=1, description='download', language=code)
                                for code in ('eng', 'fr', 'fre', 'xx', 'zzz', 'en:forced', None)])
    memory_session.flush()
    response = api_client.get('/api/system/languages?history=true', headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    assert response.get_json() == [
        {'code2': 'xx', 'code3': None, 'name': None, 'enabled': False},
        {'code2': 'en', 'code3': 'eng', 'name': 'English', 'enabled': False},
        {'code2': 'fr', 'code3': 'fra', 'name': 'French', 'enabled': False},
    ]
