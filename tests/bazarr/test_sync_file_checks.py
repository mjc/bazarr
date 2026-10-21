import os
from typing import Literal
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import TableEpisodes, TableMovies
from constants import MINIMUM_VIDEO_SIZE
from utilities import arr_files
from radarr.sync import movies
from radarr.movie_snapshot import FileState, IncompleteMovie, Movie
from radarr.sync.utils import MOVIE_DECODER
from sonarr.episode_snapshot import episodes_with_files
from sonarr.sync import episodes
from tests.helpers.arr_fixtures import episode_payload, episode_record, movie_payload
from tests.bazarr.test_sync_lookups import configure_episodes, configure_movies


@pytest.mark.parametrize('kind', ['movie', 'episode'])
@pytest.mark.parametrize('strm', [False, True])
def test_sync_stats_undersized_files_once(kind: str, strm: bool, memory_session: Session,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    files = ([record.movieFile for record in configure_movies(monkeypatch, memory_session, 3)] if kind == 'movie'
             else [record.episodeFile for record in configure_episodes(monkeypatch, memory_session, 3)])
    for file in files:
        assert file is not None
        file.size = 0
        if strm:
            file.path = file.path.replace('.mkv', '.strm')
    stats: list[str] = []

    def getsize(path: str) -> int:
        stats.append(path)
        return 0 if strm else MINIMUM_VIDEO_SIZE + 1

    monkeypatch.setattr(os.path, 'getsize', getsize)
    monkeypatch.setattr('app.config.settings.general.enable_strm_support', strm)
    monkeypatch.setattr(movies, 'store_subtitles_movie', lambda identifier: None)
    monkeypatch.setattr(episodes, 'store_subtitles', lambda identifier: None)
    if kind == 'movie':
        movies.update_movies(job_id=1)
        sizes = memory_session.execute(select(TableMovies.file_size)).scalars().all()
    else:
        episodes.sync_episodes(7, defer_search=True)
        sizes = memory_session.execute(select(TableEpisodes.file_size)).scalars().all()

    assert len(stats) == 3
    assert sizes == [0, 0, 0]  # Persist the Arr size, not the filesystem fallback.


def test_movie_size_cache_keeps_strm_setting_live(memory_session: Session,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    record = configure_movies(monkeypatch, memory_session, 1)[0]
    assert record.movieFile is not None
    record.movieFile.path = '/movie.strm'
    record.movieFile.size = 0
    getsize = Mock(return_value=0)
    monkeypatch.setattr(os.path, 'getsize', getsize)
    sizes: dict[str, int] = {}
    monkeypatch.setattr(movies.settings.general, 'enable_strm_support', False)
    assert not movies._retains_movie(movies._classify_movie(record), file_sizes=sizes)
    monkeypatch.setattr(movies.settings.general, 'enable_strm_support', True)
    assert movies._retains_movie(movies._classify_movie(record), file_sizes=sizes)
    assert movies._classify_movie(record) == (record, record.movieFile)
    assert getsize.call_count == 1


@pytest.mark.parametrize(('has_file', 'file_present', 'usable', 'expected'), [
    (True, True, True, 'ready'), (True, True, False, 'ready'),
    (True, False, True, 'preserve'), (False, True, True, 'remove'),
    (False, False, False, 'remove'),
])
def test_complete_movie_classification_separates_file_policy(
    has_file: bool, file_present: bool, usable: bool, expected: Literal['ready', 'preserve', 'remove'],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = MOVIE_DECODER.complete_from_value(movie_payload())
    assert isinstance(record, Movie)
    record.hasFile = has_file
    if not file_present:
        record.movieFile = None
    monkeypatch.setattr(movies, 'is_usable_file', Mock(return_value=usable))

    selected = movies._classify_movie(record)

    if expected == 'ready':
        assert selected == (record, record.movieFile)
    else:
        assert selected == expected
    assert movies._retains_movie(selected, file_sizes={}) is (expected == 'preserve' or (expected == 'ready' and usable))


@pytest.mark.parametrize('has_file', [None, False, True])
@pytest.mark.parametrize('file_present', [False, True])
@pytest.mark.parametrize('usable', [False, True])
def test_incomplete_movie_selection_never_processes_partial_metadata(
    has_file: bool | None, file_present: bool, usable: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = IncompleteMovie(1, has_file, FileState(10, '/movie.mkv', 123) if file_present else None)
    check = Mock(return_value=usable)
    monkeypatch.setattr(movies, 'is_usable_file', check)

    selected = movies._classify_movie(record)

    expected = record.movieFile if has_file is True and file_present else ('remove' if has_file is False else 'preserve')
    assert selected == expected
    assert movies._retains_movie(selected, file_sizes={}) is not (
        has_file is False or (has_file is True and file_present and not usable))
    assert check.call_count == int(has_file is True and file_present)


@pytest.mark.parametrize('single', [False, True])
@pytest.mark.parametrize(('state', 'expected'), [
    ('ready', 'update'), ('absent', 'remove'), ('unknown', 'preserve'), ('unusable', 'remove'),
    ('partial_usable', 'preserve'), ('partial_unusable', 'remove'),
    ('partial_unknown', 'preserve'), ('partial_absent', 'remove'),
])
def test_bulk_and_single_movie_classification_agree(
    single: bool, state: str, expected: Literal['update', 'preserve', 'remove'],
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete = configure_movies(monkeypatch, memory_session, 1)[0]
    file = complete.movieFile
    assert file is not None
    record: Movie | IncompleteMovie = complete
    if state.startswith('partial_'):
        record = IncompleteMovie(
            complete.id, None if state == 'partial_unknown' else state != 'partial_absent',
            FileState(file.id, file.path, 0 if state == 'partial_unusable' else file.size))
    elif state == 'absent':
        complete.hasFile = False
    elif state == 'unknown':
        complete.movieFile = None
    elif state == 'unusable':
        file.size = 0
    monkeypatch.setattr(movies, 'get_movies_from_radarr_api', Mock(return_value=[record]))
    monkeypatch.setattr(movies.settings.general, 'enable_strm_support', False)
    monkeypatch.setattr(os.path, 'getsize', Mock(return_value=0))
    removed = Mock(return_value=[1])
    monkeypatch.setattr(movies, 'delete_media_and_wanted_search_state', removed)

    if single:
        movies.update_one_movie(1, 'updated', defer_search=True)
    else:
        movies.update_movies(job_id=1)

    if expected == 'remove':
        removed.assert_called_once()
        assert removed.call_args.args[-1] == [1]
    else:
        removed.assert_not_called()
    assert memory_session.execute(select(TableMovies.title)).scalar_one() == (
        'Changed 1' if expected == 'update' else 'Movie')


def test_movie_classification_does_not_apply_file_policy_before_monitoring(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import update

    records = configure_movies(monkeypatch, memory_session, 2, monitored_only=True)
    records[1].monitored = False
    memory_session.execute(update(TableMovies).where(TableMovies.radarrId == 2).values(monitored='False'))
    policy = Mock(return_value=True)
    monkeypatch.setattr(movies, 'is_usable_file', policy)

    movies.update_movies(job_id=1)

    # Both files participate in deletion safety; only the monitored movie reaches processing.
    assert [call.args[0] for call in policy.call_args_list] == [
        '/movies/1.mkv', '/movies/2.mkv', '/movies/1.mkv']


def test_episode_file_selection_keeps_records_and_files_without_copying() -> None:
    missing = episode_record(episode_payload(episode_id=1))
    missing.episodeFile = None
    record = episode_record(episode_payload(episode_id=2))
    records = [missing, record]

    selected = list(episodes_with_files(records))

    assert len(selected) == 1
    episode, file = selected[0]
    assert episode is record
    assert file is record.episodeFile
    assert records == [missing, record]


def test_file_policy_handles_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    record = episode_record(episode_payload())
    assert record.episodeFile is not None
    record.episodeFile.size = 0
    monkeypatch.setattr('app.config.settings.general.enable_strm_support', False)

    def missing(path: str) -> int:
        raise FileNotFoundError(path)

    monkeypatch.setattr(os.path, 'getsize', missing)
    assert not arr_files.is_usable_file(record.episodeFile.path, record.episodeFile.size, path_replace=lambda path: path, file_sizes={})
