from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.database import TableEpisodes, TableLanguagesProfiles, TableMovies, TableShows
from radarr.movie_snapshot import IncompleteMovie
from radarr.sync import movies
from sonarr.sync import episodes, series
from sonarr.episode_snapshot import Episode
from sonarr.sync.parser import map_series
from sonarr.series_snapshot import Series, SeriesValues
from tests.bazarr.test_sync_lookups import configure_episodes, configure_movies
from tests.bazarr.test_sync_lookups import sql_reads as sql_reads
from tests.helpers.arr_fixtures import series_payload, series_record
from utilities.arr_sync_state import EPISODE_STATE, MOVIE_STATE, SERIES_STATE, values_match
from utilities.arr_sync_lookups import ArrSyncLookups
from utilities.arr_types import WriteOutcome


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


def configure_series(monkeypatch: pytest.MonkeyPatch, memory_session: Session, count: int) -> list[Series]:
    records = [series_record(series_payload(series_id=identifier))
               for identifier in range(1, count + 1)]
    for record in records:
        record.path = f'/show/{record.id}'
        memory_session.add(TableShows(**map_series(record, audio_language='[]', tags=[])))
        record.title = f'Changed {record.id}'
    memory_session.commit()
    monkeypatch.setattr(series, 'database', memory_session)
    monkeypatch.setattr(series, 'check_sonarr_rootfolder', Mock())
    monkeypatch.setattr(series, 'get_series_from_sonarr_api', Mock(side_effect=lambda **kwargs: records.copy()))
    monkeypatch.setattr(series, 'get_tags', Mock(return_value=[]))
    monkeypatch.setattr(series, 'get_profile_list', Mock(return_value=[]))
    monkeypatch.setattr(series, 'get_language_profiles', Mock(return_value=[]))
    synced = Mock()
    monkeypatch.setattr(series, 'sync_episodes', synced)
    monkeypatch.setattr(series, 'event_stream', Mock())
    monkeypatch.setattr(series.settings.sonarr, 'sync_only_monitored_series', False)
    for setting in ('serie_default_enabled', 'serie_tag_enabled', 'parse_embedded_audio_track', 'debug'):
        monkeypatch.setattr(series.settings.general, setting, False)
    monkeypatch.setattr(series, 'jobs_queue', SimpleNamespace(
        get_job=Mock(return_value=SimpleNamespace(cancel_event=Event())),
        update_job_progress=Mock(), update_job_name=Mock()))
    return records


@pytest.mark.parametrize('count', [1, 20, 205])
def test_series_reads_metadata_in_bounded_batches(count: int, memory_session: Session,
                                                 monkeypatch: pytest.MonkeyPatch, sql_reads: list[str]) -> None:
    configure_series(monkeypatch, memory_session, count)
    synced = Mock()
    monkeypatch.setattr(series, 'sync_episodes', synced)
    sql_reads.clear()

    series.update_series(job_id=1)

    assert len(sql_reads) == 1 + (count + series.SERIES_SYNC_BATCH_SIZE - 1) // series.SERIES_SYNC_BATCH_SIZE
    assert 'overview' not in sql_reads[0]
    assert 'fanart' not in sql_reads[0]
    assert all(query.count('?') <= series.SERIES_SYNC_BATCH_SIZE for query in sql_reads[1:])
    assert synced.call_count == count
    assert memory_session.execute(select(TableShows.title).order_by(TableShows.sonarrSeriesId)).scalars().all() == [
        f'Changed {identifier}' for identifier in range(1, count + 1)]


def test_series_catalogue_preserves_positions_and_first_duplicate(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second, *_ = configure_series(monkeypatch, memory_session, 4)
    duplicate = series_record(series_payload(series_id=1, title='Duplicate'))
    monkeypatch.setattr(series, 'get_series_from_sonarr_api', Mock(return_value=[None, first, duplicate, second]))
    monkeypatch.setattr(series, 'SERIES_SYNC_BATCH_SIZE', 2)
    removed = Mock()
    monkeypatch.setattr(series, 'update_one_series', removed)
    progress = Mock()
    synced = Mock()
    monkeypatch.setattr(series.jobs_queue, 'update_job_progress', progress)
    monkeypatch.setattr(series, 'sync_episodes', synced)

    series.update_series(job_id=1)

    assert memory_session.execute(select(TableShows.title).where(TableShows.sonarrSeriesId == 1)).scalar_one() == first.title
    assert [call.kwargs['progress_value'] for call in progress.call_args_list
            if 'progress_value' in call.kwargs] == [2, 4]
    assert [call.kwargs['series_id'] for call in synced.call_args_list] == [1, 2]
    removed.assert_not_called()


def test_series_counts_invalid_duplicate_and_unmonitored_records(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second, *_ = configure_series(monkeypatch, memory_session, 4)
    second.monitored = False
    memory_session.execute(update(TableShows).where(TableShows.sonarrSeriesId == second.id).values(monitored='False'))
    duplicate = series_record(series_payload(series_id=first.id))
    monkeypatch.setattr(series, 'get_series_from_sonarr_api', Mock(return_value=[None, first, duplicate, second]))
    monkeypatch.setattr(series.settings.sonarr, 'sync_only_monitored_series', True)
    monkeypatch.setattr(series, 'SERIES_SYNC_BATCH_SIZE', 1)
    removed = Mock()
    traced = Mock()
    synced = Mock()
    monkeypatch.setattr(series, 'update_one_series', removed)
    monkeypatch.setattr(series, 'trace', traced)
    monkeypatch.setattr(series, 'sync_episodes', synced)

    series.update_series(job_id=1)

    traced.assert_any_call('skipped 3 unmonitored series out of 4')
    synced.assert_called_once_with(series_id=first.id)
    removed.assert_not_called()


@pytest.mark.parametrize(('initial_enabled', 'later_enabled', 'synced_count'), [
    (False, True, 2), (True, False, 2), (True, True, 1),
])
def test_series_monitoring_preserves_initial_snapshot_and_live_setting(
    initial_enabled: bool, later_enabled: bool, synced_count: int,
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = configure_series(monkeypatch, memory_session, 2)
    records[1].monitored = False
    memory_session.execute(update(TableShows).where(TableShows.sonarrSeriesId == 2).values(monitored='False'))
    monkeypatch.setattr(series.settings.sonarr, 'sync_only_monitored_series', initial_enabled)
    monkeypatch.setattr(series, 'SERIES_SYNC_BATCH_SIZE', 1)

    def after_episode_sync(series_id: int) -> None:
        monkeypatch.setattr(series.settings.sonarr, 'sync_only_monitored_series', later_enabled)
        # A later metadata read must not replace the initial monitoring snapshot.
        memory_session.execute(update(TableShows).where(TableShows.sonarrSeriesId == 2).values(monitored='True'))

    synced = Mock(side_effect=after_episode_sync)
    monkeypatch.setattr(series, 'sync_episodes', synced)
    series.update_series(job_id=1)
    assert synced.call_count == synced_count


def test_series_deleted_between_reads_is_not_reinserted(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_series(monkeypatch, memory_session, 2)
    monkeypatch.setattr(series, 'SERIES_SYNC_BATCH_SIZE', 1)

    def after_episode_sync(series_id: int) -> None:
        if series_id == 1:
            memory_session.execute(delete(TableShows).where(TableShows.sonarrSeriesId == 2))

    monkeypatch.setattr(series, 'sync_episodes', after_episode_sync)
    series.update_series(job_id=1)
    assert memory_session.execute(select(TableShows.sonarrSeriesId)).scalars().all() == [1]


def test_series_inserted_between_reads_is_reconciled(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = configure_series(monkeypatch, memory_session, 2)
    memory_session.add(TableLanguagesProfiles(profileId=7, name='Custom', items='[]'))
    memory_session.execute(delete(TableShows).where(TableShows.sonarrSeriesId == 2))
    monkeypatch.setattr(series, 'SERIES_SYNC_BATCH_SIZE', 1)

    def after_episode_sync(series_id: int) -> None:
        if series_id == 1:
            values = map_series(records[1], audio_language='[]', tags=[], profile_id=7)
            values['title'] = 'Inserted concurrently'
            memory_session.add(TableShows(**values))
            memory_session.flush()

    monkeypatch.setattr(series, 'sync_episodes', after_episode_sync)

    series.update_series(job_id=1)

    assert memory_session.execute(select(TableShows.title, TableShows.profileId)
                                  .where(TableShows.sonarrSeriesId == 2)).one() == (records[1].title, 7)


def test_unchanged_series_do_not_emit_updates(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = configure_series(monkeypatch, memory_session, 3)
    emitted = Mock()
    monkeypatch.setattr(series, 'event_stream', emitted)
    for record in records:
        record.title = 'Series'
    series.update_series(job_id=1)
    emitted.assert_not_called()


def test_movie_catalogue_keeps_duplicate_updates_and_unknown_id_safety(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = configure_movies(monkeypatch, memory_session, 1)[0]
    assert movies._retained_movie_ids([record], file_sizes={}) == {1}
    assert movies._retained_movie_ids([record, record], file_sizes={}) is None
    assert movies._retained_movie_ids([record, IncompleteMovie(None, None, None)], file_sizes={}) is None


def test_movie_selection_preserves_progress_for_complete_records_without_files(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    absent, unknown, ready = configure_movies(monkeypatch, memory_session, 3)
    absent.hasFile = False
    unknown.movieFile = None
    records = [IncompleteMovie(None, None, None), absent, unknown, ready]
    monkeypatch.setattr(movies, 'get_movies_from_radarr_api', Mock(side_effect=lambda **kwargs: records.copy()))
    progress = Mock()
    monkeypatch.setattr(movies.jobs_queue, 'update_job_progress', progress)

    movies.update_movies(job_id=1)

    assert [call.kwargs['progress_value'] for call in progress.call_args_list
            if 'progress_value' in call.kwargs] == [2, 3, 4]
    assert memory_session.execute(select(TableMovies.title).order_by(TableMovies.radarrId)).scalars().all() == [
        'Movie', 'Movie', 'Changed 3']


@pytest.mark.parametrize('kind', ['movie', 'episode'])
@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('changed_file', [False, True])
def test_single_sync_preserves_index_events_and_search_deferral(
    kind: str, existing: bool, changed_file: bool, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if kind == 'movie':
        record = configure_movies(monkeypatch, memory_session, 1)[0]
        file = record.movieFile
        module = movies
        table = TableMovies
        indexer = 'store_subtitles_movie'
    else:
        record = configure_episodes(monkeypatch, memory_session, 1)[0]
        file = record.episodeFile
        module = episodes
        table = TableEpisodes
        indexer = 'store_subtitles'
    assert file is not None
    if changed_file:
        file.id += 1
        file.path = '/changed.mkv'
    if not existing:
        memory_session.execute(delete(table))
    indexed = Mock()
    emitted = Mock()
    pending = Mock()
    monkeypatch.setattr(module, indexer, indexed)
    monkeypatch.setattr(module, 'event_stream', emitted)
    if kind == 'movie':
        monkeypatch.setattr(module, '_is_there_missing_subtitles', Mock(return_value=True))
    monkeypatch.setattr(episodes, '_get_due_missing_episode_ids', Mock(return_value={1}))
    monkeypatch.setattr(module.os.path, 'exists', Mock(return_value=True))
    monkeypatch.setattr(module.jobs_queue, 'feed_jobs_pending_queue', pending, raising=False)

    if kind == 'movie':
        movies.update_one_movie(1, 'updated')
    else:
        episodes.sync_episodes(episode_ids=[1])

    assert indexed.call_count == int(not existing or changed_file)
    emitted.assert_called_once_with(type='movie' if kind == 'movie' else 'episode', action='update', payload=1)
    assert pending.call_count == int(not existing or changed_file)


@pytest.mark.parametrize('kind', ['movie', 'episode'])
def test_failed_single_update_does_not_defer_search(
    kind: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if kind == 'movie':
        configure_movies(monkeypatch, memory_session, 1)
        module, writer = movies, 'update_movie'
    else:
        configure_episodes(monkeypatch, memory_session, 1)
        module, writer = episodes, '_update_episode'
    pending = Mock()
    monkeypatch.setattr(module, writer, Mock(return_value='failed'))
    if kind == 'movie':
        monkeypatch.setattr(module, '_is_there_missing_subtitles', Mock(return_value=True))
    monkeypatch.setattr(episodes, '_get_due_missing_episode_ids', Mock(return_value={1}))
    monkeypatch.setattr(module.os.path, 'exists', Mock(return_value=True))
    monkeypatch.setattr(module.jobs_queue, 'feed_jobs_pending_queue', pending, raising=False)

    if kind == 'movie':
        movies.update_one_movie(1, 'updated')
    else:
        episodes.sync_episodes(episode_ids=[1])

    pending.assert_called_once()


@pytest.mark.parametrize('response', ['failed', 'empty', 'absent', 'unusable'])
def test_single_episode_preserves_failed_responses_and_removes_missing_files(
    response: str, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = configure_episodes(monkeypatch, memory_session, 1)
    if response == 'failed':
        monkeypatch.setattr(episodes, 'get_episodes_from_sonarr_api', Mock(return_value=None))
    elif response == 'empty':
        monkeypatch.setattr(episodes, 'get_episodes_from_sonarr_api', Mock(return_value=[]))
    elif response == 'absent':
        records[0].hasFile = False
        records[0].episodeFile = None
    else:
        monkeypatch.setattr(episodes, 'is_usable_file', Mock(return_value=False))
    removed = Mock(return_value=[1])
    emitted = Mock()
    monkeypatch.setattr(episodes, 'delete_media_and_wanted_search_state', removed)
    monkeypatch.setattr(episodes, 'event_stream', emitted)

    episodes.sync_episodes(episode_ids=[1])

    if response in ('absent', 'unusable'):
        removed.assert_called_once_with('series', TableEpisodes, 'sonarrEpisodeId', [1])
        emitted.assert_called_once_with(type='episode', action='delete', payload=1)
    else:
        removed.assert_not_called()
        emitted.assert_not_called()


@pytest.mark.parametrize('count', [1, 20, 205])
def test_episode_id_lists_share_one_state_read(
    count: int, memory_session: Session, monkeypatch: pytest.MonkeyPatch, sql_reads: list[str],
) -> None:
    records = configure_episodes(monkeypatch, memory_session, count)
    by_id = {record.id: record for record in records}

    def fetch(*, apikey_sonarr: str, episode_id: int) -> list[Episode]:
        return [by_id[episode_id]]

    fetched = Mock(side_effect=fetch)
    monkeypatch.setattr(episodes, 'get_episodes_from_sonarr_api', fetched)
    sql_reads.clear()

    episodes.sync_episodes(episode_ids=[*by_id, *by_id])

    assert len(sql_reads) == 1
    assert all(f'table_episodes.{column}' not in sql_reads[0] for column in ('title', 'monitored', 'audio_language'))
    assert fetched.call_count == count
    assert memory_session.execute(select(TableEpisodes.title).order_by(TableEpisodes.sonarrEpisodeId)).scalars().all() == [
        f'Changed {identifier}' for identifier in range(1, count + 1)]


def test_episode_id_list_preserves_failed_and_unrequested_rows(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = configure_episodes(monkeypatch, memory_session, 3)
    records[1].hasFile = False
    records[1].episodeFile = None

    def fetch(*, apikey_sonarr: str, episode_id: int) -> list[Episode] | None:
        return None if episode_id == 1 else [records[1]]

    def remove(media_type: str, table: type[TableEpisodes], key: str, identifiers: list[int]) -> list[int]:
        memory_session.execute(delete(table).where(table.sonarrEpisodeId.in_(identifiers)))
        return identifiers

    monkeypatch.setattr(episodes, 'get_episodes_from_sonarr_api', fetch)
    monkeypatch.setattr(episodes, 'delete_media_and_wanted_search_state', remove)
    emitted = Mock()
    monkeypatch.setattr(episodes, 'event_stream', emitted)

    episodes.sync_episodes(episode_ids=[1, 2])

    assert memory_session.execute(select(TableEpisodes.sonarrEpisodeId)).scalars().all() == [1, 3]
    emitted.assert_called_once_with(type='episode', action='delete', payload=2)


def test_episode_id_list_defers_only_unchanged_files(
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = configure_episodes(monkeypatch, memory_session, 2)
    by_id = {record.id: record for record in records}
    assert records[1].episodeFile is not None
    records[1].episodeFile.id += 1

    def fetch(*, apikey_sonarr: str, episode_id: int) -> list[Episode]:
        return [by_id[episode_id]]

    monkeypatch.setattr(episodes, 'get_episodes_from_sonarr_api', fetch)
    indexed = Mock()
    pending = Mock()
    due = Mock(return_value={1, 2})
    monkeypatch.setattr(episodes, 'store_subtitles', indexed)
    monkeypatch.setattr(episodes, '_get_due_missing_episode_ids', due)
    monkeypatch.setattr(episodes.os.path, 'exists', Mock(return_value=True))
    monkeypatch.setattr(episodes.jobs_queue, 'feed_jobs_pending_queue', pending, raising=False)

    episodes.sync_episodes(episode_ids=[1, 2])

    indexed.assert_called_once_with(2)
    due.assert_called_once_with(episode_ids={2})
    assert pending.call_args.kwargs['kwargs'] == {'no': 2}


def test_row_comparison_preserves_omitted_and_missing_nullable_fields() -> None:
    values = map_series(series_record(series_payload()), audio_language='[]', tags=[])
    assert values_match(values, values)
    assert 'profileId' not in values
    with_profile: SeriesValues = {**values, 'profileId': 7}
    assert values_match(with_profile, values)
    values['profileId'] = None
    assert not values_match(with_profile, values)
    del with_profile['profileId']
    assert not values_match(with_profile, values)


@pytest.mark.parametrize('outcome', ['updated', 'reindexed', 'failed'])
def test_movie_write_outcomes(outcome: WriteOutcome, memory_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    record = configure_movies(monkeypatch, memory_session, 1)[0]
    file = record.movieFile
    assert file is not None
    previous = memory_session.execute(MOVIE_STATE).mappings().one()
    indexed = Mock()
    emitted = Mock()
    monkeypatch.setattr(movies, 'store_subtitles_movie', indexed)
    monkeypatch.setattr(movies, 'event_stream', emitted)
    if outcome == 'reindexed':
        file.id += 1
    elif outcome == 'failed':
        memory_session.execute(update(TableMovies).values(path='/concurrent.mkv'))
    values = movies._movie_values(record, file, ArrSyncLookups([], [], []), profile_id=None)

    assert movies.update_movie(previous, values) == outcome
    assert indexed.call_count == int(outcome == 'reindexed')
    assert emitted.call_count == int(outcome != 'failed')


@pytest.mark.parametrize('outcome', ['updated', 'reindexed', 'failed'])
def test_episode_write_outcomes(outcome: WriteOutcome, memory_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    record = configure_episodes(monkeypatch, memory_session, 1)[0]
    file = record.episodeFile
    assert file is not None
    previous = memory_session.execute(EPISODE_STATE).mappings().one()
    indexed = Mock()
    emitted = Mock()
    monkeypatch.setattr(episodes, 'store_subtitles', indexed)
    monkeypatch.setattr(episodes, 'event_stream', emitted)
    if outcome == 'reindexed':
        file.id += 1
    elif outcome == 'failed':
        memory_session.execute(update(TableEpisodes).values(path='/concurrent.mkv'))
    values = episodes.map_episode(record, file, audio_language='["English"]')

    assert episodes._update_episode(values, previous) == outcome
    assert indexed.call_count == int(outcome == 'reindexed')
    assert emitted.call_count == int(outcome != 'failed')
