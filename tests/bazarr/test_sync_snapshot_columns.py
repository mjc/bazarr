from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import TableEpisodes, TableMovies, TableShows
from radarr.sync import movies
from sonarr.sync import episodes


@pytest.mark.parametrize('media_type', ['series', 'movie'])
def test_sync_snapshot_omits_cached_video_metadata_and_preserves_other_columns(
    media_type: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SnapshotFound(Exception):
        pass

    execute = Mock(side_effect=SnapshotFound)
    cached_metadata = b'cached metadata' * 65536
    if media_type == 'series':
        model = TableEpisodes
        memory_session.add(TableShows(sonarrSeriesId=1, path='/show', title='Show'))
        memory_session.flush()
        memory_session.add(TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1, path='/show/ep.mkv',
                                         title='Episode', season=1, episode=1, ffprobe_cache=cached_metadata))
        monkeypatch.setattr(episodes, 'database', SimpleNamespace(execute=execute))
        monkeypatch.setattr(episodes, 'settings', SimpleNamespace(sonarr=SimpleNamespace(apikey='key')))
        with pytest.raises(SnapshotFound):
            episodes.sync_episodes(series_id=1)
    else:
        model = TableMovies
        memory_session.add(TableMovies(radarrId=1, tmdbId=1, path='/movie.mkv', title='Movie',
                                       ffprobe_cache=cached_metadata))
        monkeypatch.setattr(movies, 'database', SimpleNamespace(execute=execute))
        monkeypatch.setattr(movies, 'settings', SimpleNamespace(
            general=SimpleNamespace(movie_default_enabled=False), radarr=SimpleNamespace(apikey='key')))
        monkeypatch.setattr(movies, 'check_radarr_rootfolder', lambda: None)
        monkeypatch.setattr(movies, 'get_movies_from_radarr_api', lambda **kwargs: [])
        for name in ('get_profile_list', 'get_tags', 'get_language_profiles'):
            monkeypatch.setattr(movies, name, lambda: [])
        monkeypatch.setattr(movies, 'jobs_queue', SimpleNamespace(
            get_job=lambda **kwargs: None, update_job_progress=lambda **kwargs: None))
        with pytest.raises(SnapshotFound):
            movies.update_movies(job_id=1)
    memory_session.flush()
    execute.assert_called_once()
    statement = execute.call_args.args[0]
    expected_columns = set(model.__table__.columns.keys()) - {'ffprobe_cache'}
    assert set(statement.selected_columns.keys()) == expected_columns
    row = memory_session.execute(statement).mappings().one()
    assert set(row) == expected_columns
    assert row['title'] == ('Episode' if media_type == 'series' else 'Movie')
    assert memory_session.execute(select(model.ffprobe_cache)).scalar_one() == cached_metadata
