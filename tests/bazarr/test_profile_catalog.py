import json
from dataclasses import FrozenInstanceError, replace

import pytest

from app import database
from app.language_profiles import ProfileCatalog, ProfileSource, preserved_profile_sources
from tests.helpers.profile_records import profile_record


def source(items='[]', profile_type='basic', profile_id=1, **values):
    return ProfileSource(profile_id, 'Test', None, items, None, None, None, None, profile_type, **values)


@pytest.mark.parametrize('flag', [True, 'True'])
def test_profile_flags_are_resolved_once(flag):
    profile = profile_record({'items': [{'id': 1, 'language': 'en', 'forced': flag, 'hi': flag,
                                         'audio_exclude': flag, 'audio_only_include': flag}]})
    assert profile.items[0].forced is True
    assert profile.items[0].hi == 'required'
    assert profile.items[0].audio_exclude is True
    assert profile.upgrade_languages == frozenset({'en:forced'})
    assert profile.hi_required_languages == frozenset({'en'})


def test_optional_flags_have_complete_defaults():
    profile = profile_record({'items': [{'id': 1, 'language': 'en'}]})
    assert profile.items[0].forced is False
    assert profile.items[0].hi == 'prefer'
    assert profile.upgrade_languages == frozenset({'en', 'en:hi'})


@pytest.mark.parametrize('raw', ['not json', '[null]', '[{"id":true,"language":"en"}]',
                                  '[{"id":1,"language":"en","hi":123}]', 'null'])
def test_invalid_profiles_remain_administratively_visible(raw):
    catalog = ProfileCatalog((source(raw), source(profile_id=2)))
    assert catalog.get(1) is None
    assert catalog.get(2) is not None
    profiles = catalog.administrative_profiles()
    assert [profile['profileId'] for profile in profiles] == [1, 2]
    assert profiles[0]['items'] == []
    assert profiles[0]['profileError']
    assert profiles[0]['_profileSource']['items'] == raw
    assert preserved_profile_sources(profiles, catalog)[1].items == raw


def test_enhanced_invalid_profile_has_safe_groups_and_lossless_roundtrip():
    raw = '{broken enhanced json'
    catalog = ProfileCatalog((source(raw, 'enhanced'),))
    submitted = catalog.administrative_profiles()
    assert submitted[0]['groups'] == []
    # The frontend sends enhanced items as a groups document.
    submitted[0]['items'] = {'groups': submitted[0].pop('groups')}
    assert preserved_profile_sources(submitted, catalog)[1].items == raw


def test_changed_preservation_source_is_rejected():
    catalog = ProfileCatalog((source('bad json'),))
    submitted = catalog.administrative_profiles()
    changed = ProfileCatalog((replace(catalog.sources[0], items='different invalid document'),))
    with pytest.raises(ValueError, match='Reload settings'):
        preserved_profile_sources(submitted, changed)


def test_edit_replaces_corrupt_items_without_carrying_preservation_metadata():
    catalog = ProfileCatalog((source('bad json'),))
    corrected = {'profileId': 1, 'items': [{'id': 1, 'language': 'en'}]}
    assert preserved_profile_sources([corrected], catalog) == {}
    assert replace(catalog.sources[0], items=json.dumps(corrected['items'])).resolve().desired_languages == {'en'}


def test_profile_catalog_and_records_are_immutable():
    catalog = ProfileCatalog((source(),))
    with pytest.raises(TypeError):
        catalog.profiles[2] = catalog.profiles[1]
    with pytest.raises(FrozenInstanceError):
        catalog.profiles[1].name = 'Changed'


def test_catalog_invalidation_refreshes_profiles(memory_session, monkeypatch):
    monkeypatch.setattr(database, 'database', memory_session)
    database.invalidate_profiles()
    memory_session.add(database.TableLanguagesProfiles(profileId=7, name='Test', items='[]'))
    memory_session.commit()
    first = database.load_profile_catalog()
    assert first.get(7).desired_languages == set()
    memory_session.execute(database.update(database.TableLanguagesProfiles).values(items='[{"id":1,"language":"en"}]'))
    memory_session.commit()
    assert database.load_profile_catalog() is first
    database.invalidate_profiles()
    assert database.load_profile_catalog().get(7).desired_languages == {'en'}
    database.invalidate_profiles()


@pytest.mark.parametrize('enhanced', [False, True])
@pytest.mark.parametrize('original', [None, 0, 1])
def test_valid_admin_profiles_use_normalized_flags_and_cached_records(mocker, enhanced, original):
    items = [{'id': 1, 'language': 'en', 'hi': True, 'forced': False, 'audio_exclude': True},
             {'id': 2, 'language': 'fr', 'hi': 'Excluded'}]
    payload = {'groups': [{'id': 9, 'operator': 'AND', 'items': items}]} if enhanced else items
    stored = replace(source(json.dumps(payload), 'enhanced' if enhanced else 'basic'), original_format=original)
    catalog = ProfileCatalog((stored,))
    decode = mocker.patch('app.language_profiles.json.loads', side_effect=AssertionError('profile parsed again'))
    listed = catalog.administrative_profiles()[0]
    decode.assert_not_called()
    assert listed['originalFormat'] is original
    assert listed['items'] == [
        {'id': 1, 'language': 'en', 'forced': 'False', 'hi': 'True',
         'audio_exclude': 'True', 'audio_only_include': 'False'},
        {'id': 2, 'language': 'fr', 'forced': 'False', 'hi': 'Excluded',
         'audio_exclude': 'False', 'audio_only_include': 'False'},
    ]
    if enhanced:
        assert listed['groups'][0]['items'][0] is listed['items'][0]
        assert listed['groups'][0]['items'][1] is listed['items'][1]


def test_admin_wire_records_are_not_shared_between_responses():
    catalog = ProfileCatalog((source('[{"id":1,"language":"en"}]'),))
    first = catalog.administrative_profiles()[0]
    first['items'][0]['language'] = 'changed'
    second = catalog.administrative_profiles()[0]
    assert second['items'][0]['language'] == 'en'
    assert catalog.get(1).items[0].language == 'en'
