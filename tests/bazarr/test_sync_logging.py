import logging

import pytest
from sqlalchemy.orm import Session

from radarr.sync import movies
from sonarr.sync import episodes, series
from tests.bazarr.test_arr_sync_state import configure_series
from tests.bazarr.test_sync_lookups import configure_episodes, configure_movies


class UnformattedTitle(str):
    def __format__(self, specification: str) -> str:
        raise AssertionError('Disabled debug messages must not format titles')


@pytest.mark.parametrize('kind', ['movie', 'series'])
def test_disabled_debug_does_not_format_titles(
    kind: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(movies.settings.general, 'debug', False)
    if kind == 'movie':
        records = configure_movies(monkeypatch, memory_session, 1)
        records[0].title = UnformattedTitle('Movie')
        movies.update_movies(job_id=1)
    else:
        records = configure_series(monkeypatch, memory_session, 1)
        records[0].title = UnformattedTitle('Series')
        series.update_series(job_id=1)


@pytest.mark.parametrize('kind', ['movie', 'series', 'episode'])
def test_sync_diagnostics_follow_active_logger(
    kind: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(movies.settings.general, 'debug', False)
    if kind == 'movie':
        configure_movies(monkeypatch, memory_session, 1)
        movies.update_movies(job_id=1)
        prefix = 'SYNC_MOVIES '
    elif kind == 'series':
        configure_series(monkeypatch, memory_session, 1)
        series.update_series(job_id=1)
        prefix = 'SYNC_SERIES '
    else:
        configure_episodes(monkeypatch, memory_session, 1)
        monkeypatch.setattr(episodes.settings.sonarr, 'sync_only_monitored_series', True)
        monkeypatch.setattr(episodes.settings.sonarr, 'sync_only_monitored_episodes', True)
        episodes.sync_episodes(7, defer_search=True)
        prefix = 'SYNC_EPISODES '
    assert any(record.getMessage().startswith(prefix) for record in caplog.records)
