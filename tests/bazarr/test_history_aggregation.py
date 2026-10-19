from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from api.history import stats
from app.database import TableHistory, TableHistoryMovie


@pytest.fixture
def history_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    now = datetime(2026, 5, 16, 12, 30)

    class Clock:
        @staticmethod
        def now() -> datetime:
            return now

    monkeypatch.setattr(stats, 'datetime', SimpleNamespace(datetime=Clock, timedelta=timedelta))
    return now


@pytest.mark.parametrize('timeframe,days', [('week', 6), ('month', 30), ('trimester', 90), ('year', 364)])
def test_history_uses_one_calendar_and_groups_nonadjacent_events(
    timeframe: str, days: int, history_clock: datetime, memory_session: Session,
    api_client: FlaskClient, api_key: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    past = history_clock - timedelta(days=days)
    for table in (TableHistory, TableHistoryMovie):
        memory_session.add_all([
            table(action=1, description='first', timestamp=history_clock - timedelta(hours=2)),
            table(action=2, description='other day', timestamp=history_clock - timedelta(days=1)),
            table(action=3, description='same day', timestamp=history_clock - timedelta(hours=1)),
            table(action=1, description='lower boundary', timestamp=past),
            table(action=1, description='too old', timestamp=past - timedelta(microseconds=1)),
            table(action=1, description='too new', timestamp=history_clock + timedelta(microseconds=1)),
            table(action=0, description='not a download', timestamp=history_clock),
        ])
    memory_session.flush()
    execute = Mock(wraps=memory_session.execute)
    monkeypatch.setattr(stats, 'database', SimpleNamespace(execute=execute))

    response = api_client.get('/api/history/stats', query_string={'timeFrame': timeframe},
                              headers={'X-API-KEY': api_key})

    assert response.status_code == 200
    payload = response.get_json()
    for kind in ('series', 'movies'):
        rows = payload[kind]
        assert len(rows) == days + 1
        assert len({row['date'] for row in rows}) == days + 1
        assert rows == sorted(rows, key=lambda row: row['date'])
        counts = {row['date']: row['count'] for row in rows}
        assert counts[history_clock.date().isoformat()] == 2
        assert counts[(history_clock - timedelta(days=1)).date().isoformat()] == 1
        assert counts[past.date().isoformat()] == 1
        assert sum(counts.values()) == 4
    assert execute.call_count == 2
    for call in execute.call_args_list:
        statement = call.args[0]
        assert len(statement.selected_columns) == 2
        assert 'GROUP BY' in str(statement)


def test_history_filters_share_the_same_aggregation(
    history_clock: datetime, memory_session: Session, api_client: FlaskClient,
    api_key: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for table in (TableHistory, TableHistoryMovie):
        memory_session.add_all([
            table(action=6, description='match', provider='wanted', language='en', timestamp=history_clock),
            table(action=1, description='action', provider='wanted', language='en', timestamp=history_clock),
            table(action=6, description='provider', provider='other', language='en', timestamp=history_clock),
            table(action=6, description='language', provider='wanted', language='fr', timestamp=history_clock),
        ])
    memory_session.flush()
    monkeypatch.setattr(stats, 'database', memory_session)
    response = api_client.get('/api/history/stats', query_string={
        'timeFrame': 'week', 'action': '6', 'provider': 'wanted', 'language': 'en',
    }, headers={'X-API-KEY': api_key})
    assert response.status_code == 200
    for rows in response.get_json().values():
        assert sum(row['count'] for row in rows) == 1


def test_history_default_is_month_and_invalid_window_is_rejected(
    history_clock: datetime, memory_session: Session, api_client: FlaskClient,
    api_key: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stats, 'database', memory_session)
    headers = {'X-API-KEY': api_key}
    response = api_client.get('/api/history/stats', headers=headers)
    assert response.status_code == 200
    assert len(response.get_json()['series']) == 31
    assert api_client.get('/api/history/stats?timeFrame=day', headers=headers).status_code == 400
