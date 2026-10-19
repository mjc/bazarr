"""
Tests for saving languages profiles through the system settings endpoint.

Invalid enhanced profiles must be rejected before anything is written, and valid enhanced
profiles must be stored with their profile type and groups.
"""
import copy
import json

import pytest

import api.system.settings as settings_api
from app.database import TableLanguagesProfiles, select
from app import database as app_database


def _item(item_id, language, **overrides):
    item = {"id": item_id, "language": language, "hi": "False", "forced": "False",
            "audio_exclude": "False", "audio_only_include": "False"}
    item.update(overrides)
    return item


def _enhanced_profile(**overrides):
    profile = {
        "profileId": 9, "name": "Enhanced", "cutoff": 2, "profileType": "enhanced",
        "items": {"groups": [
            {"id": 1, "operator": "AND", "items": [_item(1, "en"), _item(2, "fr")]},
            {"id": 2, "operator": "OR", "items": [_item(3, "de")]},
        ]},
        "mustContain": [], "mustNotContain": [], "originalFormat": None, "tag": None,
    }
    profile.update(overrides)
    return profile


def _with_groups(mutate):
    profile = _enhanced_profile()
    mutate(profile["items"]["groups"])
    return profile


INVALID_PROFILES = {
    "no groups": _enhanced_profile(items={"groups": []}),
    "items not an object": _enhanced_profile(items=[_item(1, "en")]),
    "duplicate group id": _with_groups(lambda groups: groups[1].update(id=1)),
    "group id 65535 is reserved": _with_groups(lambda groups: groups[1].update(id=65535)),
    "group id is a bool": _with_groups(lambda groups: groups[1].update(id=True)),
    "invalid operator": _with_groups(lambda groups: groups[0].update(operator="XOR")),
    "empty group": _with_groups(lambda groups: groups[1].update(items=[])),
    "duplicate language id across groups": _with_groups(
        lambda groups: groups[1]["items"].append(_item(1, "it"))),
    "invalid hearing-impaired value": _with_groups(
        lambda groups: groups[0]["items"].append(_item(4, "es", hi="Maybe"))),
    "invalid forced value": _with_groups(
        lambda groups: groups[0]["items"].append(_item(4, "es", forced="yes"))),
    "missing language code": _with_groups(
        lambda groups: groups[0]["items"].append(_item(4, ""))),
    "cutoff does not match a group": _enhanced_profile(cutoff=7),
    "unknown profile type": _enhanced_profile(profileType="advanced"),
}


@pytest.fixture
def saved(memory_session, monkeypatch):
    """Route the endpoint to an in-memory database and record whether settings were written."""
    calls = {"save_settings": 0}
    monkeypatch.setattr(settings_api, "database", memory_session)
    monkeypatch.setattr(app_database, "database", memory_session)
    app_database.invalidate_profiles()
    monkeypatch.setattr(settings_api, "event_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_api, "list_missing_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_api, "list_missing_subtitles_movies", lambda *args, **kwargs: None)

    def record_save(*args, **kwargs):
        calls["save_settings"] += 1
    monkeypatch.setattr(settings_api, "save_settings", record_save)
    yield memory_session, calls
    app_database.invalidate_profiles()


def _post_profiles(client, api_key, profiles, **extra_form):
    form = {"languages-profiles": json.dumps(profiles), **extra_form}
    return client.post("/api/system/settings", data=form, headers={"X-API-KEY": api_key})


@pytest.mark.parametrize("profile", INVALID_PROFILES.values(), ids=INVALID_PROFILES.keys())
def test_invalid_enhanced_profile_is_rejected_before_any_write(saved, api_client, api_key, profile):
    session, calls = saved

    response = _post_profiles(api_client, api_key, [copy.deepcopy(profile)], **{"languages-enabled": ["en"]})

    assert response.status_code == 400
    assert session.execute(select(TableLanguagesProfiles)).all() == []
    assert calls["save_settings"] == 0


def test_valid_enhanced_profile_is_stored_with_type_and_groups(saved, api_client, api_key):
    session, calls = saved

    response = _post_profiles(api_client, api_key, [_enhanced_profile()])

    assert response.status_code != 400
    row = session.execute(select(TableLanguagesProfiles).where(TableLanguagesProfiles.profileId == 9)).scalar_one()
    assert row.profileType == "enhanced"
    assert row.cutoff == 2
    assert json.loads(row.items) == _enhanced_profile()["items"]
    assert calls["save_settings"] == 1


def test_enhanced_cutoff_accepts_any_and_no_cutoff(saved, api_client, api_key):
    session, _ = saved

    for cutoff in (65535, None):
        response = _post_profiles(api_client, api_key, [_enhanced_profile(cutoff=cutoff)])
        assert response.status_code != 400
        row = session.execute(select(TableLanguagesProfiles).where(TableLanguagesProfiles.profileId == 9)).scalar_one()
        assert row.cutoff == cutoff


def test_basic_profile_without_type_is_still_saved_as_basic(saved, api_client, api_key):
    session, _ = saved
    basic = {
        "profileId": 3, "name": "Basic", "cutoff": None,
        "items": [_item(1, "en")], "mustContain": [], "mustNotContain": [],
        "originalFormat": None, "tag": None,
    }

    response = _post_profiles(api_client, api_key, [basic])

    assert response.status_code != 400
    row = session.execute(select(TableLanguagesProfiles).where(TableLanguagesProfiles.profileId == 3)).scalar_one()
    assert row.profileType == "basic"
    assert json.loads(row.items) == [_item(1, "en")]


@pytest.mark.parametrize('raw_items,raw_contains', [('invalid JSON', None), ('[null]', "['WEB']"),
                                                     ('[]', 'invalid text list')])
def test_invalid_profile_roundtrip_preserves_raw_fields(saved, api_client, api_key, raw_items, raw_contains):
    session, calls = saved
    session.add(TableLanguagesProfiles(profileId=1, name='Needs repair', items=raw_items,
                                       mustContain=raw_contains, mustNotContain="['x265']"))
    session.commit()
    app_database.invalidate_profiles()
    listed = api_client.get('/api/system/languages/profiles', headers={'X-API-KEY': api_key})
    profiles = listed.get_json()
    assert profiles[0]['profileId'] == 1
    assert profiles[0]['items'] == []
    response = _post_profiles(api_client, api_key, profiles)
    assert response.status_code == 204
    row = session.execute(select(TableLanguagesProfiles)).scalar_one()
    assert row.items == raw_items
    assert row.mustContain == raw_contains
    assert row.mustNotContain == "['x265']"


def test_stale_invalid_profile_roundtrip_is_rejected_before_settings_write(saved, api_client, api_key):
    session, calls = saved
    row = TableLanguagesProfiles(profileId=1, name='Needs repair', items='old broken data')
    session.add(row)
    session.commit()
    app_database.invalidate_profiles()
    profiles = app_database.list_profiles()
    row.items = 'new broken data'
    session.commit()
    app_database.invalidate_profiles()
    response = _post_profiles(api_client, api_key, profiles, **{'languages-enabled': ['en']})
    assert response.status_code == 400
    assert calls['save_settings'] == 0
    assert row.items == 'new broken data'


def test_invalid_profile_can_be_repaired_by_replacing_its_items(saved, api_client, api_key):
    session, calls = saved
    session.add(TableLanguagesProfiles(profileId=1, name='Needs repair', items='broken data'))
    session.commit()
    app_database.invalidate_profiles()
    corrected = {'profileId': 1, 'name': 'Repaired', 'profileType': 'basic', 'cutoff': None,
                 'items': [_item(1, 'en')], 'mustContain': [], 'mustNotContain': [], 'originalFormat': False}
    response = _post_profiles(api_client, api_key, [corrected])
    assert response.status_code == 204
    assert app_database.get_profile(1).desired_languages == {'en'}


def test_invalid_basic_profile_is_rejected_before_any_write(saved, api_client, api_key):
    session, calls = saved
    profile = {'profileId': 9, 'name': 'Invalid basic', 'items': [None]}
    response = _post_profiles(api_client, api_key, [profile], **{'languages-enabled': ['en']})
    assert response.status_code == 400
    assert calls['save_settings'] == 0
    assert session.execute(select(TableLanguagesProfiles)).all() == []


@pytest.mark.parametrize('enhanced', [False, True])
def test_saved_boolean_flags_have_the_frontend_string_wire_contract(saved, api_client, api_key, enhanced):
    items = [{'id': 1, 'language': 'en', 'hi': True, 'forced': False, 'audio_only_include': True}]
    payload = {'profileId': 9, 'name': 'Flags', 'cutoff': None, 'profileType': 'enhanced' if enhanced else 'basic',
               'items': {'groups': [{'id': 1, 'operator': 'AND', 'items': items}]} if enhanced else items,
               'mustContain': [], 'mustNotContain': [], 'originalFormat': None}
    assert _post_profiles(api_client, api_key, [payload]).status_code == 204
    listed = api_client.get('/api/system/languages/profiles', headers={'X-API-KEY': api_key}).get_json()[0]
    assert listed['items'] == [{'id': 1, 'language': 'en', 'hi': 'True', 'forced': 'False',
                               'audio_only_include': 'True', 'audio_exclude': 'False'}]
    assert listed['originalFormat'] is None
    if enhanced:
        assert listed['groups'][0]['items'] == listed['items']
        listed['items'] = {'groups': listed.pop('groups')}
    assert _post_profiles(api_client, api_key, [listed]).status_code == 204
    assert app_database.get_profile(9).items[0].hi == 'required'
