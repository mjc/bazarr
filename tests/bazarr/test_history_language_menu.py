from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask.testing import FlaskClient
from sqlalchemy import event
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import ORMExecuteState, Session

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


def test_history_language_menu_streams_rows_without_reordering_unknown_languages(
    memory_session: Session, api_client: FlaskClient, api_key: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(languages, 'database', memory_session)
    registry = LanguageRegistry([
        {'code2': 'en', 'code3': 'eng', 'code3b': None, 'name': 'English'},
        {'code2': 'fr', 'code3': 'fra', 'code3b': 'fre', 'name': 'French'},
    ])
    monkeypatch.setattr(get_languages, '_language_registry', registry)
    for table in (TableHistory, TableHistoryMovie):
        tokens = ['zz', 'xx', 'en:forced', 'eng', None, 'fr', 'fre', 'en:hi', ''] * 200
        memory_session.add_all([table(action=1, description='download', language=code) for code in tokens])
    memory_session.flush()
    batch_sizes: list[int] = []
    orm_queries: list[bool] = []
    original_yield_per = CursorResult.yield_per

    def record_batch(result: CursorResult[tuple[str]], size: int) -> CursorResult[tuple[str]]:
        batch_sizes.append(size)
        return original_yield_per(result, size)

    def record_query(state: ORMExecuteState) -> None:
        orm_queries.append(state.is_orm_statement)

    monkeypatch.setattr(CursorResult, 'yield_per', record_batch)
    event.listen(memory_session, 'do_orm_execute', record_query)
    try:
        response = api_client.get('/api/system/languages?history=true', headers={'X-API-KEY': api_key})
    finally:
        event.remove(memory_session, 'do_orm_execute', record_query)
    assert response.status_code == 200
    assert response.get_json() == [
        {'code2': 'zz', 'code3': None, 'name': None, 'enabled': False},
        {'code2': 'xx', 'code3': None, 'name': None, 'enabled': False},
        {'code2': 'en', 'code3': 'eng', 'name': 'English', 'enabled': False},
        {'code2': 'fr', 'code3': 'fra', 'name': 'French', 'enabled': False},
    ]
    assert batch_sizes == [500, 500]
    assert orm_queries == [False, False]


def test_history_language_menu_closes_the_cursor_if_language_lookup_fails(
    memory_session: Session, api_client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(languages, 'database', memory_session)
    memory_session.add(TableHistory(action=1, description='download', language='en'))
    memory_session.flush()
    registry = SimpleNamespace(from_code2=Mock(side_effect=RuntimeError('language lookup failed')))
    monkeypatch.setattr(languages, 'get_language_registry', lambda: registry)
    closed: list[CursorResult[tuple[str]]] = []
    original_close = CursorResult.close

    def record_close(result: CursorResult[tuple[str]]) -> None:
        closed.append(result)
        original_close(result)

    monkeypatch.setattr(CursorResult, 'close', record_close)
    with api_client.application.test_request_context('/api/system/languages?history=true'):
        with pytest.raises(RuntimeError, match='language lookup failed'):
            unwrap(languages.Languages.get)(languages.Languages())
    assert len(closed) == 1
