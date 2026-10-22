import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.database import TableEpisodes, TableMovies
from radarr.sync import movies
from sonarr.sync import episodes
from tests.bazarr.test_sync_lookups import configure_episodes, configure_movies
from tests.bazarr.test_sync_lookups import sql_reads as sql_reads
from utilities.arr_sync_state import EPISODE_STATE, MOVIE_STATE, SERIES_STATE


@pytest.mark.parametrize('kind', ['movie', 'episode'])
def test_sync_does_not_select_or_replace_probe_cache(kind: str, memory_session: Session,
                                                    monkeypatch: pytest.MonkeyPatch, sql_reads: list[str]) -> None:
    if kind == 'movie':
        configure_movies(monkeypatch, memory_session, 2)
        table = TableMovies
    else:
        configure_episodes(monkeypatch, memory_session, 2)
        table = TableEpisodes
    memory_session.execute(update(table).values(ffprobe_cache=b'cached probe result'))
    sql_reads.clear()

    if kind == 'movie':
        movies.update_movies(job_id=1)
    else:
        episodes.sync_episodes(7, defer_search=True)

    assert all('ffprobe_cache' not in query for query in sql_reads)
    assert memory_session.execute(select(table.ffprobe_cache)).scalars().all() == [b'cached probe result'] * 2



@pytest.mark.parametrize('statement', [MOVIE_STATE, EPISODE_STATE, SERIES_STATE])
def test_sync_projection_excludes_unmapped_state(statement: Select[tuple[str | int | None, ...]]) -> None:
    assert not {'ffprobe_cache', 'created_at_timestamp', 'updated_at_timestamp',
                'missing_subtitles_calculated'} & set(statement.selected_columns.keys())



def test_state_read_preserves_session_autoflush(memory_session: Session) -> None:
    memory_session.add(TableMovies(radarrId=1, title='Pending', tmdbId='1', path='/movie'))
    assert memory_session.execute(MOVIE_STATE).mappings().one()['title'] == 'Pending'
