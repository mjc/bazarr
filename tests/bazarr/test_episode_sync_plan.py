from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import event

from app.database import TableShows, TableEpisodes
from constants import MINIMUM_VIDEO_SIZE
from sonarr.episode_snapshot import complete_episode_snapshot
from sonarr.info import SonarrCapabilities, SonarrConnection
from sonarr.sync import episodes as sync
from sonarr.sync import utils
from utilities.arr import parse_arr_version


def episode(*, has_file=True):
    result = {'id': 101, 'seriesId': 7, 'title': 'Pilot', 'monitored': True,
              'hasFile': has_file, 'seasonNumber': 1, 'episodeNumber': 1}
    if has_file:
        result['episodeFileId'] = 11
        result['episodeFile'] = episode_file()
    return result


def episode_file():
    return {'id': 11, 'path': '/show/episode.mkv', 'size': MINIMUM_VIDEO_SIZE + 1,
            'quality': {'quality': {'name': 'HDTV-1080p'}}, 'language': {'name': 'English'}}


def connection(version='3.0.0.1'):
    parsed = parse_arr_version(version)
    assert parsed is not None
    return SonarrConnection('http://arr/api/v3/', 'key', 30, SonarrCapabilities(parsed))


@pytest.fixture
def live_sync(memory_session, monkeypatch):
    memory_session.add(TableShows(sonarrSeriesId=7, title='Show', path='/show'))
    memory_session.flush()
    memory_session.add(TableEpisodes(sonarrEpisodeId=101, sonarrSeriesId=7, title='Pilot',
                                     path='/show/episode.mkv', season=1, episode=1, episode_file_id=11,
                                     monitored='True'))
    memory_session.commit()
    monkeypatch.setattr(sync, 'database', memory_session)
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection())
    monkeypatch.setattr(sync.settings.sonarr, 'sync_only_monitored_series', False)
    monkeypatch.setattr(sync.settings.sonarr, 'sync_only_monitored_episodes', False)
    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', False)
    monkeypatch.setattr(sync.settings.general, 'debug', False)
    monkeypatch.setattr(sync.settings.general, 'enable_strm_support', False)
    effects = {}
    for name in ('delete_media_and_wanted_search_state', 'event_stream', 'store_subtitles', 'send_notifications'):
        effects[name] = Mock(return_value=[])
        monkeypatch.setattr(sync, name, effects[name])
    statements = []
    event.listen(memory_session, 'do_orm_execute', lambda state: statements.append(state.statement))
    return memory_session, effects, statements


@pytest.mark.parametrize('files', [None, [], [dict(episode_file(), id=99)]])
@pytest.mark.parametrize('single', [False, True])
def test_missing_secondary_files_cannot_change_existing_rows(live_sync, monkeypatch, files, single):
    session, effects, statements = live_sync
    payload = episode()
    payload.pop('episodeFile')
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: payload if single else [payload])
    monkeypatch.setattr(sync, 'get_episodesFiles_from_sonarr_api',
                        lambda **kwargs: (files[0] if files else None) if single else files)
    if single:
        sync.sync_one_episode(101)
    else:
        sync.sync_episodes(7)
    assert all(statement.is_select for statement in statements)
    assert session.get(TableEpisodes, 101).path == '/show/episode.mkv'
    for effect in effects.values():
        effect.assert_not_called()


@pytest.mark.parametrize('single', [False, True])
def test_unknown_version_cannot_fetch_or_reconcile(live_sync, monkeypatch, single):
    _, effects, statements = live_sync
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: None)
    fetch = Mock()
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', fetch)
    sync.sync_one_episode(101) if single else sync.sync_episodes(7)
    fetch.assert_not_called()
    assert all(statement.is_select for statement in statements)
    for effect in effects.values():
        effect.assert_not_called()


@pytest.mark.parametrize('invalid', [None, {}, [None], [], [dict(episode(), seriesId=9)],
                                     [dict(episode(), hasFile=None)], [episode(), episode()]])
def test_invalid_catalogues_do_not_reconcile(live_sync, monkeypatch, invalid):
    _, effects, statements = live_sync
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: invalid)
    monkeypatch.setattr(sync, 'get_episodesFiles_from_sonarr_api', lambda **kwargs: [episode_file()])
    sync.sync_episodes(7)
    assert all(statement.is_select for statement in statements)
    for effect in effects.values():
        effect.assert_not_called()


@pytest.mark.parametrize('version,secondary', [('2.0.0.1', False), ('3.0.0.1', True),
                                                ('4.0.9.2420', True), ('4.0.9.2421', False)])
@pytest.mark.parametrize('single', [False, True])
def test_valid_snapshot_selects_file_api_and_updates_real_row(live_sync, monkeypatch, version, secondary, single):
    session, effects, _ = live_sync
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection(version))
    payload = episode()
    payload['title'] = 'Updated'
    if secondary:
        payload.pop('episodeFile')
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: payload if single else [payload])
    files = Mock(return_value=episode_file() if single else [episode_file()])
    monkeypatch.setattr(sync, 'get_episodesFiles_from_sonarr_api', files)
    sync.sync_one_episode(101, defer_search=True) if single else sync.sync_episodes(7, defer_search=True)
    assert files.call_count == int(secondary)
    session.expire_all()
    assert session.get(TableEpisodes, 101).title == 'Updated'
    effects['delete_media_and_wanted_search_state'].assert_not_called()


def test_snapshot_reuses_decoded_records_and_does_not_mutate_on_failed_validation():
    payload = [episode()]
    payload[0].pop('episodeFile')
    before = deepcopy(payload)
    assert complete_episode_snapshot(payload, [], separate_files=True) is None
    assert payload == before
    files = [episode_file()]
    result = complete_episode_snapshot(payload, files, separate_files=True)
    assert result is not None
    assert result.episodes is payload
    assert result.episodes[0]['episodeFile'] is files[0]


def test_empty_files_are_complete_when_episodes_have_no_media():
    assert complete_episode_snapshot([episode(has_file=False)], [], separate_files=True) is not None


@pytest.mark.parametrize('single', [False, True])
def test_explicit_absence_of_media_still_authorizes_removal(live_sync, monkeypatch, single):
    _, effects, _ = live_sync
    payload = episode(has_file=False)
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: payload if single else [payload])
    monkeypatch.setattr(sync, 'get_episodesFiles_from_sonarr_api', lambda **kwargs: [])
    sync.sync_one_episode(101) if single else sync.sync_episodes(7)
    effects['delete_media_and_wanted_search_state'].assert_called_once_with(
        'series', TableEpisodes, 'sonarrEpisodeId', [101])


@pytest.mark.parametrize('status', [401, 404, 500])
def test_series_http_error_keeps_original_exception(monkeypatch, status):
    error = requests.HTTPError('Original failure')
    error.response = requests.Response()
    error.response.status_code = status
    monkeypatch.setattr(utils, 'request_json', Mock(side_effect=error))
    with pytest.raises(requests.HTTPError) as raised:
        utils.get_series_from_sonarr_api('key', 7, connection=connection())
    assert raised.value is error


def test_series_http_error_without_response_is_not_dereferenced(monkeypatch):
    error = requests.HTTPError('No response')
    monkeypatch.setattr(utils, 'request_json', Mock(side_effect=error))
    with pytest.raises(requests.HTTPError) as raised:
        utils.get_series_from_sonarr_api('key', connection=connection())
    assert raised.value is error


@pytest.mark.parametrize('error', [requests.ConnectionError(), requests.Timeout(),
                                    requests.JSONDecodeError('bad', '', 0)])
def test_transport_or_decode_failure_is_not_an_empty_catalogue(monkeypatch, error):
    monkeypatch.setattr(utils, 'request_json', Mock(side_effect=error))
    assert utils.get_series_from_sonarr_api('key', connection=connection()) is None


def test_inline_monitored_lookup_uses_file_id_without_top_level_reference(live_sync, monkeypatch):
    session, effects, _ = live_sync
    stored = session.get(TableEpisodes, 101)
    stored.monitored = 'False'
    session.commit()
    payload = episode()
    payload.pop('episodeFileId')
    payload['monitored'] = False
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection('4.0.9.2421'))
    monkeypatch.setattr(sync.settings.sonarr, 'sync_only_monitored_series', True)
    monkeypatch.setattr(sync.settings.sonarr, 'sync_only_monitored_episodes', True)
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: [payload])
    sync.sync_episodes(7, defer_search=True)
    effects['delete_media_and_wanted_search_state'].assert_not_called()
    effects['store_subtitles'].assert_not_called()


def test_series_audio_language_fallback_is_loaded_once_per_plan(live_sync, monkeypatch):
    from sonarr.sync import parser
    session, _, statements = live_sync
    session.get(TableShows, 7).audio_language = "['English']"
    session.commit()
    monkeypatch.setattr(parser, 'database', session)
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection('4.0.9.2421'))
    first = episode()
    first['episodeFile'].pop('language')
    second = deepcopy(first)
    second['id'] = 102
    second['episodeNumber'] = 2
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: [first, second])
    sync.sync_episodes(7, defer_search=True)
    audio_queries = [statement for statement in statements
                     if str(statement).startswith('SELECT table_shows.audio_language')]
    assert len(audio_queries) == 1
    session.expire_all()
    assert session.get(TableEpisodes, 101).audio_language == "['English']"
    assert session.get(TableEpisodes, 102).audio_language == "['English']"


def test_null_optional_file_metadata_and_null_absent_file_are_accepted():
    payload = episode()
    payload['episodeFile'].update(language=None, languages=None, mediaInfo=None)
    assert complete_episode_snapshot([payload]) is not None
    absent = dict(episode(has_file=False), episodeFile=None)
    assert complete_episode_snapshot([absent]) is not None
    missing = dict(episode(), episodeFile=None)
    assert complete_episode_snapshot([missing]) is None


def test_single_episode_accepts_authoritative_series_reassignment(live_sync, monkeypatch):
    session, effects, _ = live_sync
    session.add(TableShows(sonarrSeriesId=8, title='New Series', path='/new'))
    session.commit()
    payload = dict(episode(), seriesId=8)
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection('4.0.9.2421'))
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: payload)
    sync.sync_one_episode(101, defer_search=True)
    session.expire_all()
    assert session.get(TableEpisodes, 101).sonarrSeriesId == 8
    effects['delete_media_and_wanted_search_state'].assert_not_called()


def test_episode_rechecks_current_file_before_deciding_to_skip_indexing(live_sync, monkeypatch):
    from sqlalchemy import update
    session, effects, _ = live_sync
    payload = episode()
    payload['title'] = 'Updated'
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection('4.0.9.2421'))
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: [payload])
    prepare = sync._prepare_episode_sync

    def prepare_with_interleaved_file_change(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        session.execute(update(TableEpisodes).where(TableEpisodes.sonarrEpisodeId == 101)
                        .values(path='/other.mkv', episode_file_id=20))
        return plan

    monkeypatch.setattr(sync, '_prepare_episode_sync', prepare_with_interleaved_file_change)
    sync.sync_episodes(7, defer_search=True)
    session.expire_all()
    assert session.get(TableEpisodes, 101).path == '/show/episode.mkv'
    assert session.get(TableEpisodes, 101).title == 'Updated'
    effects['store_subtitles'].assert_called_once_with(101)


def test_comparison_rows_are_released_before_subtitle_indexing(live_sync, monkeypatch):
    from weakref import ref
    from types import SimpleNamespace
    session, _, _ = live_sync
    payload = episode()
    payload['episodeFile']['path'] = '/updated.mkv'
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection('4.0.9.2421'))
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', lambda **kwargs: [payload])
    references = []
    first_query = True

    class ComparisonRow(dict):
        pass

    def execute(statement):
        nonlocal first_query
        result = session.execute(statement)
        if first_query:
            first_query = False
            rows = [ComparisonRow(row) for row in result.mappings()]
            references.extend(ref(row) for row in rows)
            return SimpleNamespace(mappings=lambda: iter(rows))
        return result

    def store(episode_id):
        assert episode_id == 101
        assert references and all(reference() is None for reference in references)

    monkeypatch.setattr(sync, 'database', SimpleNamespace(execute=execute))
    monkeypatch.setattr(sync, 'store_subtitles', store)
    sync.sync_episodes(7, defer_search=True)


def test_episode_monitored_comparison_is_read_after_the_http_snapshot(live_sync, monkeypatch):
    from sqlalchemy import update
    session, effects, _ = live_sync
    session.get(TableEpisodes, 101).monitored = 'False'
    session.commit()
    payload = episode()
    payload['monitored'] = False
    payload['episodeFile']['path'] = '/updated.mkv'

    def fetch(**kwargs):
        session.execute(update(TableEpisodes).where(TableEpisodes.sonarrEpisodeId == 101)
                        .values(monitored='True'))
        return [payload]

    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args: connection('4.0.9.2421'))
    monkeypatch.setattr(sync, 'get_episodes_from_sonarr_api', fetch)
    monkeypatch.setattr(sync.settings.sonarr, 'sync_only_monitored_series', True)
    monkeypatch.setattr(sync.settings.sonarr, 'sync_only_monitored_episodes', True)
    sync.sync_episodes(7, defer_search=True)
    session.expire_all()
    assert session.get(TableEpisodes, 101).path == '/updated.mkv'
    assert session.get(TableEpisodes, 101).monitored == 'False'
    effects['store_subtitles'].assert_called_once_with(101)
