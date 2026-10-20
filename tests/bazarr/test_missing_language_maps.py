from collections import defaultdict
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import insert, literal, select, union_all

from api import response_data
from app.database import TableFailedSubtitleAttempts, TableMissingSubtitles
from subtitles import wanted_state


POLICY = {'delay': timedelta(days=7), 'delta': timedelta(days=1),
          'initial_search_cutoff': 100.0, 'latest_search_cutoff': 10.0}
CODES = ['fr:hi', 'en', 'pt-BR', 'en:forced', 'zz:opaque']
LOADERS = ['api', 'missing', 'due-filtered', 'due-full', 'iterator']


def load(mode, policy=None):
    if mode == 'api':
        return response_data.load_missing_languages('series', select(TableMissingSubtitles.media_id).distinct())
    if mode == 'missing':
        return wanted_state.get_missing_languages_map('series', [2, 1, 3, 1])
    if mode == 'due-filtered':
        return wanted_state.get_due_missing_languages_map('series', [2, 1, 3, 1], policy)
    if mode == 'due-full':
        return wanted_state.get_due_missing_languages_map('series', adaptive_search_policy=policy)
    return {media_id: values for batch in wanted_state.iter_due_missing_languages_maps(
        'series', adaptive_search_policy=policy, batch_size=1) for media_id, values in batch.items()}


@pytest.fixture
def language_rows(memory_session, monkeypatch):
    monkeypatch.setattr(response_data, 'database', memory_session)
    monkeypatch.setattr(wanted_state, 'database', memory_session)
    monkeypatch.setattr(wanted_state, 'get_adaptive_search_policy', lambda: None)
    monkeypatch.setattr(wanted_state, 'WANTED_STATE_ID_QUERY_BATCH_SIZE', 1)
    memory_session.execute(insert(TableMissingSubtitles), [
        {'media_type': 'series', 'media_id': media_id, 'language': code}
        for media_id in (1, 2) for code in CODES
    ])
    memory_session.execute(insert(TableMissingSubtitles), [
        {'media_type': 'movie', 'media_id': 1, 'language': 'unrelated'},
    ])
    memory_session.execute(insert(TableFailedSubtitleAttempts), [
        {'media_type': 'series', 'media_id': 1, 'language': code,
         'initial_attempt_at': initial, 'latest_attempt_at': latest}
        for code, initial, latest in [('fr:hi', 0, 20), ('en', 0, 5), ('en:forced', 0, 20)]
    ])
    yield memory_session


@pytest.mark.parametrize('mode', LOADERS)
@pytest.mark.parametrize('adaptive', [False, True])
def test_language_maps_preserve_values_order_and_share_codes(language_rows, mode, adaptive):
    actual = load(mode, POLICY if adaptive else None)
    due = adaptive and mode not in ('api', 'missing')
    expected = {1: ['en', 'pt-BR', 'zz:opaque'] if due else CODES, 2: CODES}
    if mode in ('missing', 'due-filtered'):
        expected[3] = []
        assert list(actual) == [2, 1, 3]
    assert actual == expected
    assert type(actual) is (defaultdict if mode == 'api' else dict)
    first = actual[1][actual[1].index('pt-BR')]
    second = actual[2][actual[2].index('pt-BR')]
    assert first is second


def test_shared_collector_preserves_duplicate_rows(memory_session):
    duplicate_rows = union_all(
        select(literal(1).label('media_id'), literal('en').label('language')),
        select(literal(1).label('media_id'), literal('en').label('language')),
    ).subquery()
    languages = {}
    wanted_state.append_missing_language_rows(
        memory_session, select(duplicate_rows.c.media_id, duplicate_rows.c.language), languages, {},
    )
    assert languages == {1: ['en', 'en']}
    assert languages[1][0] is languages[1][1]


@pytest.mark.parametrize('mode', LOADERS)
def test_language_maps_preserve_session_autoflush_and_no_autoflush(memory_session, monkeypatch, mode):
    monkeypatch.setattr(response_data, 'database', memory_session)
    monkeypatch.setattr(wanted_state, 'database', memory_session)
    monkeypatch.setattr(wanted_state, 'get_adaptive_search_policy', lambda: None)
    memory_session.add(TableMissingSubtitles(media_type='series', media_id=1, language='en'))
    with memory_session.no_autoflush:
        assert load(mode).get(1, []) == []
    assert load(mode)[1] == ['en']


@pytest.mark.parametrize('mode', ['missing', 'due-filtered'])
def test_empty_filtered_language_maps_do_not_query_or_resolve_policy(monkeypatch, mode):
    execute, policy = Mock(), Mock()
    monkeypatch.setattr(wanted_state, 'database', SimpleNamespace(execute=execute))
    monkeypatch.setattr(wanted_state, 'get_adaptive_search_policy', policy)
    result = (wanted_state.get_missing_languages_map('series', []) if mode == 'missing'
              else wanted_state.get_due_missing_languages_map('series', []))
    assert result == {}
    execute.assert_not_called()
    policy.assert_not_called()


def test_streaming_result_is_closed_before_iterator_yields(language_rows, monkeypatch):
    execute = language_rows.execute
    results = []

    def record(statement, *args, **kwargs):
        result = execute(statement, *args, **kwargs)
        if len(statement.selected_columns) == 2:
            assert 'yield_per' not in statement.get_execution_options()
            assert 'stream_results' not in statement.get_execution_options()
            bounded = Mock(wraps=result.yield_per)
            monkeypatch.setattr(result, 'yield_per', bounded)
            results.append(result)
        return result

    monkeypatch.setattr(language_rows, 'execute', record)
    iterator = wanted_state.iter_due_missing_languages_maps('series', batch_size=1)
    first = next(iterator)
    assert first == {1: CODES}
    assert results and all(result.closed for result in results)
    assert all(result.yield_per.call_args.args == (500,) for result in results)
    second = next(iterator)
    assert second == {2: CODES}
    assert all(result.closed for result in results)
    assert first[1][2] is second[2][2]
    with pytest.raises(StopIteration):
        next(iterator)


def test_streaming_result_closes_on_collection_error():
    class UnhashableCode(str):
        def __hash__(self):
            raise ValueError('Cannot collect this language')

    class Rows:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def yield_per(self, size):
            assert size == 500
            return self

        def __iter__(self):
            yield 1, UnhashableCode('en')

    rows = Rows()
    session = SimpleNamespace(execute=lambda statement: rows)
    statement = select(TableMissingSubtitles.media_id, TableMissingSubtitles.language)
    with pytest.raises(ValueError, match='Cannot collect this language'):
        wanted_state.append_missing_language_rows(session, statement, {}, {})
    assert rows.closed
