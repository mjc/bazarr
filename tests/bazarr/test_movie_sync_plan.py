from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from weakref import ref

import pytest
from sqlalchemy import event

from app.database import TableMovies
from constants import MINIMUM_VIDEO_SIZE
from radarr.sync import movies as sync
from tests.helpers.arr_fixtures import movie_payload, radarr_connection


@pytest.fixture
def movie_sync(memory_session, monkeypatch):
    memory_session.add(TableMovies(radarrId=1, tmdbId='1', title='Old', path='/movie.mkv',
                                   movie_file_id=10, monitored='True'))
    memory_session.commit()
    monkeypatch.setattr(sync, 'database', memory_session)
    monkeypatch.setattr(sync, 'get_radarr_connection', Mock(return_value=radarr_connection()))
    monkeypatch.setattr(sync, 'check_radarr_rootfolder', lambda **kwargs: None)
    monkeypatch.setattr(sync.settings.general, 'movie_default_enabled', False)
    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', False)
    monkeypatch.setattr(sync.settings.general, 'movie_tag_enabled', False)
    monkeypatch.setattr(sync.settings.general, 'enable_strm_support', False)
    monkeypatch.setattr(sync.settings.radarr, 'sync_only_monitored_movies', False)
    monkeypatch.setattr(sync, 'get_profile_list', lambda **kwargs: [])
    monkeypatch.setattr(sync, 'get_tags', lambda **kwargs: [])
    monkeypatch.setattr(sync, 'get_language_profiles', lambda: [])
    monkeypatch.setattr(sync, 'jobs_queue', SimpleNamespace(
        get_job=lambda **kwargs: SimpleNamespace(cancel_event=Event()),
        update_job_progress=Mock(), update_job_name=Mock()))
    deleted = Mock(return_value=[])
    monkeypatch.setattr(sync, 'delete_media_and_wanted_search_state', deleted)
    events = Mock()
    store = Mock()
    monkeypatch.setattr(sync, 'event_stream', events)
    monkeypatch.setattr(sync, 'store_subtitles_movie', store)
    statements = []
    event.listen(memory_session, 'do_orm_execute', lambda state: statements.append(state.statement))
    return memory_session, deleted, events, store, statements


@pytest.mark.parametrize('payload', [None, {}, {'error': 'unavailable'}, [None], [{'id': True}],
                                     [{'id': 1, 'hasFile': True, 'movieFile': None}],
                                     [{'id': 1, 'hasFile': True, 'movieFile': {'id': 10, 'path': '/movie.mkv', 'size': 'bad'}}]])
def test_invalid_movie_catalogue_preserves_rows(movie_sync, monkeypatch, payload):
    session, deleted, events, store, statements = movie_sync
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: payload)
    sync.update_movies(job_id=1)
    deleted.assert_not_called()
    events.assert_not_called()
    store.assert_not_called()
    assert all(statement.is_select for statement in statements)
    assert session.get(TableMovies, 1).title == 'Old'


@pytest.mark.parametrize('single', [False, True])
def test_unknown_capabilities_prevent_fetch_and_effects(movie_sync, monkeypatch, single):
    _, deleted, events, store, statements = movie_sync
    monkeypatch.setattr(sync, 'get_radarr_connection', lambda *args, **kwargs: None)
    fetch = Mock()
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', fetch)
    sync.update_one_movie(1, 'updated') if single else sync.update_movies(job_id=1)
    fetch.assert_not_called()
    deleted.assert_not_called()
    events.assert_not_called()
    store.assert_not_called()
    assert all(statement.is_select for statement in statements)


def test_empty_movie_catalogue_authorizes_removal_without_metadata_fetch(movie_sync, monkeypatch):
    _, deleted, _, _, _ = movie_sync
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: [])
    profiles = Mock(side_effect=AssertionError('Empty catalogue needs no profiles'))
    monkeypatch.setattr(sync, 'get_profile_list', profiles)
    sync.update_movies(job_id=1)
    profiles.assert_not_called()
    deleted.assert_called_once_with('movie', TableMovies, 'radarrId', [1])


@pytest.mark.parametrize('single', [False, True])
@pytest.mark.parametrize('payload', [{'id': 1, 'hasFile': False},
                                     {'id': 1, 'hasFile': True, 'movieFile': {'id': 10, 'path': '/movie.mkv', 'size': 1}}])
def test_explicit_file_absence_or_unusable_size_authorizes_removal(movie_sync, monkeypatch, single, payload):
    _, deleted, _, _, _ = movie_sync
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: payload if single else [payload])
    monkeypatch.setattr(sync, 'get_movie_file_size_from_db', lambda path: 0)
    sync.update_one_movie(1, 'updated') if single else sync.update_movies(job_id=1)
    deleted.assert_called_once_with('movie', TableMovies, 'radarrId', [1])


def test_raw_movie_graph_is_released_before_subtitle_indexing(movie_sync, monkeypatch):
    session, deleted, _, _, _ = movie_sync
    raw_references = []

    class RemoteMovie(dict):
        pass

    def fetch(**kwargs):
        raw = RemoteMovie(movie_payload(1, 'Updated', '/updated.mkv'))
        raw['unused'] = {'nested': ['unneeded data'] * 100}
        raw_references.append(ref(raw))
        return [raw]

    def store(movie_id):
        assert movie_id == 1
        assert all(reference() is None for reference in raw_references)

    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', fetch)
    monkeypatch.setattr(sync, 'store_subtitles_movie', store)
    sync.update_movies(job_id=1)
    session.expire_all()
    assert session.get(TableMovies, 1).title == 'Updated'
    deleted.assert_not_called()


def test_eligibility_and_monitored_reads_are_not_repeated(movie_sync, monkeypatch):
    session, _, _, store, statements = movie_sync
    payload = movie_payload(1, 'Updated', '/movie.mkv', size=1)
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: [payload])
    actual_size = Mock(return_value=MINIMUM_VIDEO_SIZE + 1)
    monkeypatch.setattr(sync, 'get_movie_file_size_from_db', actual_size)
    monkeypatch.setattr(sync.settings.radarr, 'sync_only_monitored_movies', True)
    sync.update_movies(job_id=1)
    actual_size.assert_called_once_with('/movie.mkv')
    selects = [statement for statement in statements if statement.is_select]
    assert len(selects) == 2
    assert set(selects[1].selected_columns.keys()) == {'movie_file_id', 'path', 'monitored'}
    store.assert_not_called()
    session.expire_all()
    assert session.get(TableMovies, 1).title == 'Updated'


@pytest.mark.parametrize('dependency', ['get_profile_list', 'get_tags'])
def test_metadata_fetch_failure_preserves_existing_movie(movie_sync, monkeypatch, dependency):
    session, deleted, events, store, _ = movie_sync
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: [movie_payload(1, 'Updated')])
    monkeypatch.setattr(sync, dependency, lambda **kwargs: None)
    sync.update_movies(job_id=1)
    deleted.assert_not_called()
    events.assert_not_called()
    store.assert_not_called()
    assert session.get(TableMovies, 1).title == 'Old'


@pytest.mark.parametrize('single', [False, True])
def test_nullable_optional_file_metadata_preserves_unknown_languages(movie_sync, monkeypatch, single):
    session, deleted, _, _, _ = movie_sync
    payload = movie_payload(1, 'Updated', '/movie.mkv')
    payload['movieFile']['languages'] = [{'name': None}, {'name': None}]
    payload['movieFile']['mediaInfo'] = None
    payload['images'] = None
    payload['originalLanguage'] = None
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: payload if single else [payload])
    sync.update_one_movie(1, 'updated', defer_search=True) if single else sync.update_movies(job_id=1)
    session.expire_all()
    assert session.get(TableMovies, 1).audio_language == '[None, None]'
    deleted.assert_not_called()


def test_single_movie_does_not_accept_boolean_id_for_deletion(movie_sync, monkeypatch):
    _, deleted, _, _, _ = movie_sync
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: {'id': True, 'hasFile': False})
    sync.update_one_movie(1, 'updated')
    deleted.assert_not_called()


def test_added_movie_event_uses_the_same_validated_path(movie_sync, monkeypatch):
    session, _, _, _, _ = movie_sync
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: movie_payload(2, 'New', '/new.mkv', file_id=20))
    sync.update_one_movie(2, 'added', defer_search=True)
    assert session.get(TableMovies, 2).title == 'New'


@pytest.mark.parametrize('single', [False, True])
def test_legacy_movie_metadata_uses_one_resolved_capability(movie_sync, monkeypatch, single):
    session, _, _, _, _ = movie_sync
    payload = movie_payload(1, 'Updated', '/movie.mkv')
    payload['movieFile']['mediaInfo'] = {'videoFormat': 'AVC', 'audioFormat': 'AC-3', 'audioLanguages': ''}
    payload['alternativeTitles'] = [{'title': 'Alternate'}]
    monkeypatch.setattr(sync, 'get_radarr_connection', Mock(return_value=radarr_connection('0.2.0.1')))
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: payload if single else [payload])
    sync.update_one_movie(1, 'updated', defer_search=True) if single else sync.update_movies(job_id=1)
    session.expire_all()
    stored = session.get(TableMovies, 1)
    assert stored.alternativeTitles == "['Alternate']"
    assert stored.audio_language == "['']"
    assert stored.video_codec == 'h264'
    assert stored.audio_codec == 'AC3'
    sync.get_radarr_connection.assert_called_once()


def test_movie_rechecks_current_file_before_deciding_to_skip_indexing(movie_sync, monkeypatch):
    from sqlalchemy import update
    session, _, _, store, _ = movie_sync
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api',
                        lambda **kwargs: [movie_payload(1, 'Updated', '/movie.mkv')])
    prepare = sync._prepare_movie_sync

    def prepare_with_interleaved_file_change(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        session.execute(update(TableMovies).where(TableMovies.radarrId == 1)
                        .values(path='/other.mkv', movie_file_id=20))
        return plan

    monkeypatch.setattr(sync, '_prepare_movie_sync', prepare_with_interleaved_file_change)
    sync.update_movies(job_id=1)
    session.expire_all()
    assert session.get(TableMovies, 1).path == '/movie.mkv'
    assert session.get(TableMovies, 1).title == 'Updated'
    store.assert_called_once_with(1)


@pytest.mark.parametrize('embedded', [False, True])
def test_fresh_monitored_mismatch_forces_update_even_with_unchanged_metadata_and_audio(movie_sync, monkeypatch, embedded):
    from sqlalchemy import update
    session, _, _, _, _ = movie_sync
    payload = movie_payload(1, 'Movie', '/movie.mkv')
    payload['monitored'] = False
    canonical = sync.movieParser(payload, action='update', tags_dict=[], language_profiles=[],
                                 movie_default_profile=None, audio_profiles=[], general=sync.settings.general,
                                 capabilities=radarr_connection().capabilities)
    session.execute(update(TableMovies).where(TableMovies.radarrId == 1).values(canonical))
    session.commit()
    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', embedded)
    monkeypatch.setattr(sync.settings.radarr, 'sync_only_monitored_movies', True)
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: [payload])
    probe = Mock(return_value=['English'])
    monkeypatch.setattr(sync, 'embedded_audio_reader', probe)
    prepare = sync._prepare_movie_sync

    def prepare_with_interleaved_monitor_change(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        assert plan is not None and len(plan.changes) == 1
        assert plan.changes[0].metadata_changed is False
        session.execute(update(TableMovies).where(TableMovies.radarrId == 1).values(monitored='True'))
        return plan

    monkeypatch.setattr(sync, '_prepare_movie_sync', prepare_with_interleaved_monitor_change)
    sync.update_movies(job_id=1)
    session.expire_all()
    assert session.get(TableMovies, 1).monitored == 'False'
    assert probe.call_count == int(embedded)


def test_still_unmonitored_movie_does_not_probe_embedded_audio(movie_sync, monkeypatch):
    session, _, events, store, _ = movie_sync
    session.get(TableMovies, 1).monitored = 'False'
    session.commit()
    payload = movie_payload(1, 'New metadata', '/updated.mkv')
    payload['monitored'] = False
    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', True)
    monkeypatch.setattr(sync.settings.radarr, 'sync_only_monitored_movies', True)
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: [payload])
    probe = Mock(side_effect=AssertionError('Still unmonitored files must not be probed'))
    monkeypatch.setattr(sync, 'embedded_audio_reader', probe)
    sync.update_movies(job_id=1)
    probe.assert_not_called()
    events.assert_not_called()
    store.assert_not_called()
    assert session.get(TableMovies, 1).title == 'Old'


def test_raw_movie_graph_is_released_before_embedded_audio_probe(movie_sync, monkeypatch):
    references = []

    class RemoteMovie(dict):
        pass

    def fetch(**kwargs):
        raw = RemoteMovie(movie_payload(1, 'Updated', '/updated.mkv'))
        references.append(ref(raw))
        return [raw]

    def probe(*args, **kwargs):
        assert references and all(reference() is None for reference in references)
        return ['English']

    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', True)
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', fetch)
    monkeypatch.setattr(sync, 'embedded_audio_reader', probe)
    sync.update_movies(job_id=1)


@pytest.mark.parametrize('changed_field', ['metadata', 'audio'])
def test_movie_reads_current_file_after_deferred_audio_probe(movie_sync, monkeypatch, changed_field):
    from sqlalchemy import update
    session, _, _, store, _ = movie_sync
    initial = movie_payload(1, 'Movie', '/movie.mkv')
    canonical = sync.movieParser(initial, action='update', tags_dict=[], language_profiles=[],
                                 movie_default_profile=None, audio_profiles=[], general=sync.settings.general,
                                 capabilities=radarr_connection().capabilities)
    session.execute(update(TableMovies).where(TableMovies.radarrId == 1).values(canonical))
    session.commit()
    payload = movie_payload(1, 'Updated' if changed_field == 'metadata' else 'Movie', '/movie.mkv')
    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', True)
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: [payload])

    def probe(*args, **kwargs):
        session.execute(update(TableMovies).where(TableMovies.radarrId == 1)
                        .values(path='/other.mkv', movie_file_id=20))
        return ['French'] if changed_field == 'audio' else ['English']

    monkeypatch.setattr(sync, 'embedded_audio_reader', probe)
    sync.update_movies(job_id=1)
    session.expire_all()
    assert session.get(TableMovies, 1).path == '/movie.mkv'
    assert session.get(TableMovies, 1).title == payload['title']
    store.assert_called_once_with(1)


@pytest.mark.parametrize('guard', [False, True])
def test_deferred_probe_only_reads_before_probe_when_monitored_guard_needs_it(movie_sync, monkeypatch, guard):
    _, _, _, _, statements = movie_sync
    payload = movie_payload(1, 'Updated', '/movie.mkv')
    payload['monitored'] = False
    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', True)
    monkeypatch.setattr(sync.settings.radarr, 'sync_only_monitored_movies', guard)
    monkeypatch.setattr(sync, 'get_movies_from_radarr_api', lambda **kwargs: [payload])
    monkeypatch.setattr(sync, 'embedded_audio_reader', lambda *args, **kwargs: ['English'])
    sync.update_movies(job_id=1)
    selects = [statement for statement in statements if statement.is_select]
    assert len(selects) == 2 + int(guard)
    assert all(set(statement.selected_columns.keys()) == {'movie_file_id', 'path', 'monitored'}
               for statement in selects[1:])
