import json

from app.language_profiles import Profile, ProfileCatalog, ProfileSource


def profile_record(payload=None, *, profile_id=44) -> Profile:
    data = dict(payload or {})
    items = data.get('items', [])
    groups = data.get('groups')
    if groups is not None:
        profile_type, items = 'enhanced', {'groups': groups}
    else:
        profile_type = data.get('profileType', 'basic')
    # Older fixtures omitted ids, which are supplied by the actual editor.
    if isinstance(items, list):
        items = [dict(item, id=item.get('id', index)) if isinstance(item, dict) else item
                 for index, item in enumerate(items, 1)]
    return ProfileSource(data.get('profileId', profile_id), data.get('name', 'Test'), data.get('cutoff'),
                         json.dumps(items), str(data.get('mustContain', [])), str(data.get('mustNotContain', [])),
                         data.get('originalFormat'), data.get('tag'), profile_type).resolve()


def profile_catalog(*profiles: Profile) -> ProfileCatalog:
    sources = tuple(ProfileSource(profile.profile_id, profile.name, profile.cutoff,
                    json.dumps({'groups': [group.wire() for group in profile.groups]}) if profile.groups is not None
                    else json.dumps([item.wire() for item in profile.items]),
                    str(list(profile.must_contain)), str(list(profile.must_not_contain)), int(profile.original_format),
                    profile.tag, profile.profile_type) for profile in profiles)
    return ProfileCatalog(sources)
