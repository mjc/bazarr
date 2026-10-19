import json

import pytest

from app import database


def test_missing_profile_id_does_not_load_profiles(monkeypatch):
    def unexpected_load():
        pytest.fail("a missing ID should not load profiles")
    monkeypatch.setattr(database, "list_profiles", unexpected_load)
    assert database.get_profile(None) is None


def _item(item_id, language):
    return {"id": item_id, "language": language, "hi": "False", "forced": "False",
            "audio_exclude": "False", "audio_only_include": "False"}


ENHANCED_GROUPS = [
    {"id": 1, "operator": "AND", "items": [_item(1, "en"), _item(2, "fr")]},
    {"id": 2, "operator": "OR", "items": [_item(3, "de")]},
]


def test_parse_profile_items_basic_returns_flat_list():
    raw = json.dumps([_item(1, "en")])
    assert database.parse_profile_items("basic", raw) == ([_item(1, "en")], None)


def test_parse_profile_items_enhanced_flattens_groups():
    raw = json.dumps({"groups": ENHANCED_GROUPS})
    items, groups = database.parse_profile_items("enhanced", raw)
    assert [item["id"] for item in items] == [1, 2, 3]
    assert groups == ENHANCED_GROUPS


@pytest.fixture
def profiles_session(memory_session, monkeypatch):
    monkeypatch.setattr(database, "database", memory_session)
    database.list_profiles.invalidate()
    yield memory_session
    database.list_profiles.invalidate()


def test_list_profiles_exposes_profile_type_and_groups(profiles_session):
    profiles_session.add(database.TableLanguagesProfiles(
        profileId=1, name="Basic", cutoff=None, items=json.dumps([_item(1, "en")])))
    profiles_session.add(database.TableLanguagesProfiles(
        profileId=2, name="Enhanced", cutoff=2, items=json.dumps({"groups": ENHANCED_GROUPS}),
        profileType="enhanced"))
    profiles_session.commit()

    profiles = {profile["profileId"]: profile for profile in database.list_profiles()}

    assert profiles[1]["profileType"] == "basic"
    assert "groups" not in profiles[1]
    assert [item["id"] for item in profiles[1]["items"]] == [1]
    assert profiles[2]["profileType"] == "enhanced"
    assert profiles[2]["groups"] == ENHANCED_GROUPS
    assert [item["id"] for item in profiles[2]["items"]] == [1, 2, 3]


def test_new_profile_defaults_to_basic_type(profiles_session):
    profiles_session.add(database.TableLanguagesProfiles(profileId=3, name="Default", items="[]"))
    profiles_session.commit()
    assert profiles_session.execute(
        database.select(database.TableLanguagesProfiles.profileType)
        .where(database.TableLanguagesProfiles.profileId == 3)).scalar_one() == "basic"


def test_raw_json_upgrade_helpers_skip_enhanced_profiles(profiles_session):
    basic_items = [{"id": 1, "language": "en", "hi": "only", "forced": "False"}]
    profiles_session.add(database.TableLanguagesProfiles(
        profileId=1, name="Basic", cutoff=None, items=json.dumps(basic_items)))
    profiles_session.add(database.TableLanguagesProfiles(
        profileId=2, name="Enhanced", cutoff=None, items=json.dumps({"groups": ENHANCED_GROUPS}),
        profileType="enhanced"))
    profiles_session.commit()

    database.upgrade_languages_profile_values()
    database.fix_languages_profiles_with_duplicate_ids()

    stored = {row.profileId: row.items for row in profiles_session.execute(
        database.select(database.TableLanguagesProfiles.profileId, database.TableLanguagesProfiles.items))}
    assert json.loads(stored[1])[0]["hi"] == "True"
    assert json.loads(stored[2]) == {"groups": ENHANCED_GROUPS}


@pytest.mark.parametrize('raw_items', ['null', '{}', '[null]', '[null, {"id": 1, "language": "en"}]', '[{"id": 1, "language": null}]', 'not json'])
def test_malformed_profile_items_return_none(raw_items):
    assert database.parse_profile_items('basic', raw_items) is None


def test_list_and_lookup_interfaces_do_not_confuse_missing_profile(profiles_session):
    profiles_session.add(database.TableLanguagesProfiles(profileId=1, name='English', items='[]'))
    profiles_session.commit()
    assert [profile['profileId'] for profile in database.list_profiles()] == [1]
    assert database.get_profile(1)['name'] == 'English'
    assert database.get_profile(999) is None
