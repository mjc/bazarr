from types import SimpleNamespace
from tests.helpers.arr_fixtures import radarr_connection
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import ORMExecuteState, Session

from app.database import TableEpisodes, TableMovies, TableShows
from radarr.sync import movies
from sonarr.sync import episodes
from sonarr.sync import series


@pytest.mark.parametrize('media_type', ['episode', 'movie', 'series'])
def test_sync_snapshot_omits_cached_video_metadata_and_preserves_other_columns(
    media_type: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SnapshotFound(Exception):
        pass

    execute = Mock(side_effect=SnapshotFound)
    cached_metadata = b'cached metadata' * 65536
    if media_type == 'episode':
        model = TableEpisodes
        memory_session.add(TableShows(sonarrSeriesId=1, path='/show', title='Show'))
        memory_session.flush()
        memory_session.add(TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1, path='/show/ep.mkv',
                                         title='Episode', season=1, episode=1, ffprobe_cache=cached_metadata))
        monkeypatch.setattr(episodes, 'database', SimpleNamespace(execute=execute))
        monkeypatch.setattr(episodes, 'settings', SimpleNamespace(sonarr=SimpleNamespace(apikey='key')))
        with pytest.raises(SnapshotFound):
            episodes.sync_episodes(series_id=1)
    elif media_type == 'movie':
        model = TableMovies
        monkeypatch.setattr(movies, 'get_radarr_connection', lambda *args: radarr_connection())
        memory_session.add(TableMovies(radarrId=1, tmdbId=1, path='/movie.mkv', title='Movie',
                                       ffprobe_cache=cached_metadata))
        monkeypatch.setattr(movies, 'database', SimpleNamespace(execute=execute))
        monkeypatch.setattr(movies, 'settings', SimpleNamespace(
            general=SimpleNamespace(movie_default_enabled=False), radarr=SimpleNamespace(apikey='key')))
        monkeypatch.setattr(movies, 'check_radarr_rootfolder', lambda: None)
        monkeypatch.setattr(movies, 'get_movies_from_radarr_api', lambda **kwargs: [])
        for name in ('get_profile_list', 'get_tags', 'get_language_profiles'):
            monkeypatch.setattr(movies, name, lambda **kwargs: [])
        monkeypatch.setattr(movies, 'jobs_queue', SimpleNamespace(
            get_job=lambda **kwargs: SimpleNamespace(cancel_event=SimpleNamespace(is_set=lambda: False)), update_job_progress=lambda **kwargs: None))
        with pytest.raises(SnapshotFound):
            movies.update_movies(job_id=1)
    else:
        model = TableShows
        memory_session.add(TableShows(sonarrSeriesId=1, path='/show', title='Show'))
        monkeypatch.setattr(series, 'database', SimpleNamespace(execute=execute))
        with pytest.raises(SnapshotFound):
            series.update_one_series(1, action='updated')
    memory_session.flush()
    execute.assert_called_once()
    statement = execute.call_args.args[0]
    expected_columns = set(model.__table__.columns.keys()) - {'ffprobe_cache'}
    assert set(statement.selected_columns.keys()) == expected_columns
    expected_statement = select(*(
        getattr(model, name) for name in model.__table__.columns.keys() if name != 'ffprobe_cache'
    ))
    if media_type == 'episode':
        expected_statement = expected_statement.where(TableEpisodes.sonarrSeriesId == 1)
    elif media_type == 'series':
        expected_statement = expected_statement.where(TableShows.sonarrSeriesId == 1)
    for dialect in (sqlite.dialect(), postgresql.dialect()):
        original_sql = expected_statement.compile(dialect=dialect)
        snapshot_sql = statement.compile(dialect=dialect)
        assert str(snapshot_sql) == str(original_sql)
        assert snapshot_sql.params == original_sql.params
    expected = memory_session.execute(expected_statement).mappings().one()
    orm_statements: list[bool] = []

    def record_execution(state: ORMExecuteState) -> None:
        orm_statements.append(state.is_orm_statement)

    event.listen(memory_session, 'do_orm_execute', record_execution)
    try:
        row = memory_session.execute(statement).mappings().one()
    finally:
        event.remove(memory_session, 'do_orm_execute', record_execution)
    assert orm_statements == [False]
    assert set(row) == expected_columns
    assert row == expected
    if media_type != 'series':
        assert memory_session.execute(select(model.ffprobe_cache)).scalar_one() == cached_metadata
