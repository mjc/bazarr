from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from weakref import ref

import pytest
import requests
from dynaconf.utils.boxing import DynaBox

from app.database import TableShows
from sonarr.sync import series as sync
from tests.helpers.arr_fixtures import series_payload, sonarr_connection


@pytest.fixture
def series_sync(memory_session, monkeypatch):
    memory_session.add(TableShows(sonarrSeriesId=7, title='Old', path='/series/path'))
    memory_session.commit()
    monkeypatch.setattr(sync, 'database', memory_session)
    monkeypatch.setattr(sync, 'get_sonarr_connection', Mock(return_value=sonarr_connection()))
    monkeypatch.setattr(sync, 'check_sonarr_rootfolder', lambda **kwargs: None)
    monkeypatch.setattr(sync.settings.general, 'serie_default_enabled', False)
    monkeypatch.setattr(sync.settings.general, 'parse_embedded_audio_track', False)
    monkeypatch.setattr(sync.settings.general, 'serie_tag_enabled', False)
    monkeypatch.setattr(sync.settings.sonarr, 'sync_only_monitored_series', False)
    monkeypatch.setattr(sync, 'get_profile_list', lambda **kwargs: [])
    monkeypatch.setattr(sync, 'get_tags', lambda **kwargs: [])
    monkeypatch.setattr(sync, 'get_language_profiles', lambda: [])
    monkeypatch.setattr(sync, 'jobs_queue', SimpleNamespace(
        get_job=lambda **kwargs: SimpleNamespace(cancel_event=Event()),
        update_job_progress=Mock(), update_job_name=Mock()))
    deleted = Mock()
    monkeypatch.setattr(sync, 'delete_series_and_wanted_search_state', deleted)
    events = Mock()
    monkeypatch.setattr(sync, 'event_stream', events)
    monkeypatch.setattr(sync, 'sync_episodes', Mock())
    return memory_session, deleted, events


@pytest.mark.parametrize('payload', [None, {}, {'error': 'unavailable'}, [None],
                                     [{'id': 7, 'title': 'Incomplete'}]])
def test_invalid_catalogue_preserves_series(series_sync, monkeypatch, payload):
    session, deleted, events = series_sync
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', lambda **kwargs: payload)
    sync.update_series(job_id=1)
    deleted.assert_not_called()
    events.assert_not_called()
    session.expire_all()
    assert session.get(TableShows, 7).title == 'Old'


def test_successful_empty_catalogue_authorizes_series_deletion(series_sync, monkeypatch):
    _, deleted, _ = series_sync
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', lambda **kwargs: [])
    sync.update_series(job_id=1)
    deleted.assert_called_once_with(7)


def test_unknown_capabilities_prevent_catalogue_fetch(series_sync, monkeypatch):
    _, deleted, _ = series_sync
    monkeypatch.setattr(sync, 'get_sonarr_connection', lambda *args, **kwargs: None)
    fetch = Mock()
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', fetch)
    sync.update_series(job_id=1)
    fetch.assert_not_called()
    deleted.assert_not_called()


@pytest.mark.parametrize('failure', [requests.HTTPError('No response'), requests.Timeout(), requests.ConnectionError()])
def test_failed_catalogue_request_preserves_series(series_sync, monkeypatch, failure):
    _, deleted, events = series_sync
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', Mock(side_effect=failure))
    sync.update_series(job_id=1)
    deleted.assert_not_called()
    events.assert_not_called()


def test_raw_catalogue_is_released_before_nested_episode_sync(series_sync, monkeypatch):
    session, deleted, _ = series_sync
    raw_references = []

    class RemoteSeries(dict):
        pass

    def fetch(**kwargs):
        raw = RemoteSeries(series_payload(7, 'Updated'))
        raw['unused_metadata'] = {'nested': ['large remote-only data'] * 100}
        raw_references.append(ref(raw))
        return [raw]

    def sync_episodes(series_id, **kwargs):
        assert series_id == 7
        assert all(reference() is None for reference in raw_references)
        assert kwargs['connection'] is sync.get_sonarr_connection.return_value

    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', fetch)
    monkeypatch.setattr(sync, 'sync_episodes', sync_episodes)
    sync.update_series(job_id=1)
    session.expire_all()
    assert session.get(TableShows, 7).title == 'Updated'
    sync.get_sonarr_connection.assert_called_once()
    deleted.assert_not_called()


def test_full_sync_passes_its_settings_sections_to_each_episode_sync(
    series_sync, monkeypatch: pytest.MonkeyPatch,
) -> None:
    general_section = sync.settings.general
    sonarr_section = sync.settings.sonarr

    class Settings:
        general_reads = 0
        sonarr_reads = 0

        @property
        def general(self) -> DynaBox:
            self.general_reads += 1
            return general_section

        @property
        def sonarr(self) -> DynaBox:
            self.sonarr_reads += 1
            return sonarr_section

    settings = Settings()
    monkeypatch.setattr(sync, 'settings', settings)
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', lambda **kwargs: [
        series_payload(7, 'Updated'), dict(series_payload(8, 'New'), path='/new')])
    sync.update_series(job_id=1)
    assert settings.general_reads == settings.sonarr_reads == 1
    assert sync.sync_episodes.call_count == 2
    for call in sync.sync_episodes.call_args_list:
        assert call.kwargs['general'] is general_section
        assert call.kwargs['sonarr'] is sonarr_section


def test_incomplete_known_series_cannot_remove_unseen_series(series_sync, monkeypatch):
    session, deleted, _ = series_sync
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api',
                        lambda **kwargs: [dict(series_payload(8, 'Good'), path='/another'), {'id': 9, 'title': 'Incomplete'}])
    sync.update_series(job_id=1)
    deleted.assert_not_called()
    assert session.get(TableShows, 7) is not None
    assert session.get(TableShows, 8) is not None


@pytest.mark.parametrize('dependency', ['get_profile_list', 'get_tags'])
def test_failed_metadata_fetch_preserves_stored_series(series_sync, monkeypatch, dependency):
    session, deleted, events = series_sync
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', lambda **kwargs: [series_payload(7, 'Updated')])
    monkeypatch.setattr(sync, dependency, lambda **kwargs: None)
    sync.update_series(job_id=1)
    deleted.assert_not_called()
    events.assert_not_called()
    session.expire_all()
    assert session.get(TableShows, 7).title == 'Old'


def test_empty_series_catalogue_does_not_request_unneeded_metadata(series_sync, monkeypatch):
    _, deleted, _ = series_sync
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', lambda **kwargs: [])
    profiles = Mock(side_effect=AssertionError('Empty catalogue needs no profile enrichment'))
    monkeypatch.setattr(sync, 'get_profile_list', profiles)
    sync.update_series(job_id=1)
    profiles.assert_not_called()
    deleted.assert_called_once_with(7)


def test_default_profile_only_applies_to_inserted_series(series_sync, monkeypatch):
    from app.database import TableLanguagesProfiles
    session, _, _ = series_sync
    session.add_all([TableLanguagesProfiles(profileId=3, name='Existing', items='[]'),
                     TableLanguagesProfiles(profileId=9, name='Default', items='[]')])
    session.get(TableShows, 7).profileId = 3
    session.commit()
    monkeypatch.setattr(sync.settings.general, 'serie_default_enabled', True)
    monkeypatch.setattr(sync.settings.general, 'serie_default_profile', 9)
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', lambda **kwargs: [
        series_payload(7, 'Existing'), dict(series_payload(8, 'New'), path='/new')])
    sync.update_series(job_id=1)
    session.expire_all()
    assert session.get(TableShows, 7).profileId == 3
    assert session.get(TableShows, 8).profileId == 9


def test_processed_canonical_series_is_released_before_episode_sync(series_sync, monkeypatch):
    references = []
    parse = sync.seriesParser

    class ParsedSeries(dict):
        pass

    def tracked_parse(*args, **kwargs):
        result = ParsedSeries(parse(*args, **kwargs))
        references.append(ref(result))
        return result

    def sync_episodes(**kwargs):
        assert all(reference() is None for reference in references)

    monkeypatch.setattr(sync, 'get_series_from_sonarr_api', lambda **kwargs: [series_payload(7, 'Updated')])
    monkeypatch.setattr(sync, 'seriesParser', tracked_parse)
    monkeypatch.setattr(sync, 'sync_episodes', sync_episodes)
    sync.update_series(job_id=1)


def test_invalid_date_metadata_cannot_authorize_reconciliation(series_sync, monkeypatch):
    session, deleted, events = series_sync
    monkeypatch.setattr(sync, 'get_series_from_sonarr_api',
                        lambda **kwargs: [dict(series_payload(7), lastAired='not a date')])
    sync.update_series(job_id=1)
    deleted.assert_not_called()
    events.assert_not_called()
    session.expire_all()
    assert session.get(TableShows, 7).title == 'Old'
