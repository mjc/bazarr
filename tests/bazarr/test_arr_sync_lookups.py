from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from radarr.sync import movies
from sonarr.sync import series
from app.database import TableLanguagesProfiles, TableMovies, TableShows
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.bazarr.test_sync_lookups import configure_movies
from tests.helpers.arr_fixtures import series_payload, series_record
from utilities.arr_sync_lookups import ArrSyncLookups, default_profile_id
from utilities.arr_types import ArrTag, AudioProfile, KEEP_PROFILE, ProfileAssignment


def test_tag_order_and_duplicate_catalogue_entries_are_preserved() -> None:
    tags: list[ArrTag] = [{'id': 2, 'label': 'second'}, {'id': 1, 'label': 'first'},
                          {'id': 2, 'label': 'second again'}, {'id': 3, 'label': 'first'}]
    lookups = ArrSyncLookups(tags, [], [])
    assert lookups.tags([1, 2, 2, 999]) == ['second', 'first', 'second again']
    assert lookups.tags([1, 3]) == ['first', 'first']
    assert lookups.tags([]) == []


def test_profile_priority_uses_profile_order() -> None:
    profiles = [(7, 'Preferred', 'a'), (9, 'Other', 'b'), (10, 'Duplicate', 'a'), (11, 'No tag', None)]
    lookups = ArrSyncLookups([], profiles, [])
    assert lookups.profile_id(['b', 'a'], None, enabled=True, remove_tags=()) == 7
    assert lookups.profile_id(['b'], None, enabled=True, remove_tags=()) == 9
    assert lookups.profile_id(['unknown'], None, enabled=True, remove_tags=()) is None
    assert ArrSyncLookups([], [(0, 'Zero', 'a')], []).profile_id(['a'], 8, enabled=True, remove_tags=()) == 8


def test_audio_profiles_preserve_duplicates_and_id_equality() -> None:
    profiles: list[AudioProfile] = [(1, 'English'), (1.0, 'French'), ('1', 'German'), (True, 'Spanish')]
    lookups = ArrSyncLookups([], [], profiles)
    assert lookups.audio_language_values.get(1, '[]') == "['English', 'French', 'Spanish']"
    assert lookups.audio_language_values.get(2, '[]') == '[]'
    assert lookups.audio_language_values.get(1, '[]') is lookups.audio_language_values.get(1, '[]')


def test_catalogue_is_consumed_once() -> None:
    seen: list[int] = []

    def catalogue() -> Iterator[ArrTag]:
        for identifier in range(100):
            seen.append(identifier)
            yield {'id': identifier, 'label': str(identifier)}

    lookups = ArrSyncLookups(catalogue(), [(7, 'Language', '2')], [(1, 'English')])
    for _ in range(20):
        assert lookups.tags([2, 7]) == ['2', '7']
        assert lookups.profile_id(['2'], None, enabled=True, remove_tags=()) == 7
        assert lookups.audio_language_values.get(1, '[]') == "['English']"
    assert seen == list(range(100))


@pytest.mark.parametrize('kind', ['movie', 'series'])
@pytest.mark.parametrize(('media_tags', 'expected_tags', 'profile_id'), [
    ([], [], None), ([999], [], None), ([1], ['a'], 7), ([2], ['b'], 9),
    ([2, 1], ['a', 'b'], 7), ([1, 1], ['a'], 7),
])
@pytest.mark.parametrize('remove_tags', [[], ['b'], ['B'], ['unknown'], ['a', 'a'], ['a', 'unknown'], ['', 'β']])
def test_syncs_use_shared_lookups_and_live_tag_settings(
    kind: str, media_tags: list[int], expected_tags: list[str], profile_id: int | None,
    remove_tags: list[str], memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags: list[ArrTag] = [{'id': 1, 'label': 'a'}, {'id': 2, 'label': 'b'}]
    profiles = [(7, 'Preferred', 'a'), (9, 'Other', 'b')]
    audio_profiles = [(1, 'English')]
    memory_session.add_all([TableLanguagesProfiles(profileId=identifier, name=name, items='[]')
                            for identifier, name in ((7, 'Preferred'), (9, 'Other'))])
    if kind == 'movie':
        movie = configure_movies(monkeypatch, memory_session, 1)[0]
        movie.tags = media_tags
        movie.qualityProfileId = 1
        monkeypatch.setattr(movies, 'get_tags', lambda: tags)
        monkeypatch.setattr(movies, 'get_language_profiles', lambda: profiles)
        monkeypatch.setattr(movies, 'get_profile_list', lambda: audio_profiles)

        def sync() -> None:
            movies.update_movies(job_id=1)

        table = TableMovies
    else:
        show = series_record(series_payload())
        show.tags = media_tags
        show.languageProfileId = 1
        monkeypatch.setattr(series, 'database', memory_session)
        monkeypatch.setattr(series, 'event_stream', Mock())
        monkeypatch.setattr(series, 'get_series_from_sonarr_api', Mock(return_value=[show]))
        monkeypatch.setattr(series, 'get_tags', lambda: tags)
        monkeypatch.setattr(series, 'get_language_profiles', lambda: profiles)
        monkeypatch.setattr(series, 'get_profile_list', lambda: audio_profiles)
        monkeypatch.setattr('app.config.settings.general.serie_default_enabled', False)

        def sync() -> None:
            series.update_one_series(show.id, 'updated', sync_episodes_after_update=False)

        table = TableShows
    monkeypatch.setattr('app.config.settings.general.parse_embedded_audio_track', False)
    monkeypatch.setattr('app.config.settings.general.movie_tag_enabled', True)
    monkeypatch.setattr('app.config.settings.general.serie_tag_enabled', True)
    monkeypatch.setattr('app.config.settings.general.remove_profile_tags', [])

    sync()
    assert memory_session.execute(select(table.tags, table.profileId, table.audio_language)).one() == (
        str(expected_tags), profile_id, "['English']")
    monkeypatch.setattr('app.config.settings.general.remove_profile_tags', remove_tags)
    expected_profile = None if set(expected_tags) & set(remove_tags) else profile_id
    sync()
    assert memory_session.execute(select(table.profileId)).scalar_one() == expected_profile


@pytest.mark.parametrize(('configured', 'expected'), [('', None), (0, 0), (7, 7), ('7', 7), ('0', 0),
                                                        ('None', None), ('invalid', None), ('  ', None), ('7.0', None)])
def test_default_profile_id_is_normalized(configured: int | str, expected: int | None) -> None:
    assert default_profile_id(configured) == expected


@pytest.mark.parametrize(('default', 'enabled', 'tags', 'removed', 'expected'), [
    (KEEP_PROFILE, False, ['a'], [], KEEP_PROFILE),
    (None, False, ['a'], [], None),
    (3, False, ['a'], ['a'], 3),
    (3, True, ['a', 'b'], [], 7),
    (3, True, ['a', 'b'], ['b'], None),
    (KEEP_PROFILE, True, ['unknown'], [], KEEP_PROFILE),
    (None, True, ['unknown'], [], None),
    (0, True, ['unknown'], [], 0),
    (3, True, ['zero'], [], 3),
    (None, True, ['zero'], [], None),
])
def test_profile_assignment_preserves_default_override_and_removal(
    default: ProfileAssignment, enabled: bool, tags: list[str], removed: list[str],
    expected: ProfileAssignment,
) -> None:
    lookups = ArrSyncLookups([], [(7, 'First', 'a'), (9, 'Second', 'b'), (0, 'Zero', 'zero')], [])
    assert lookups.profile_id(tags, default, enabled=enabled, remove_tags=removed) == expected
