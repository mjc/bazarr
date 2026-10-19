# coding=utf-8

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, NamedTuple

from literals import HI_EXCLUDED
from utilities.json_types import JsonObject, JsonValue
from utilities.text_list import parse_string_list

HiMode = Literal['prefer', 'required', 'excluded']
ProfileType = Literal['basic', 'enhanced']


class SubtitleRequirement(NamedTuple):
    language: str
    forced: bool
    hi: HiMode


@dataclass(frozen=True, slots=True)
class ProfileItem:
    item_id: int
    language: str
    forced: bool = False
    hi: HiMode = 'prefer'
    audio_exclude: bool = False
    audio_only_include: bool = False
    requirement: SubtitleRequirement = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'requirement', SubtitleRequirement(self.language, self.forced, self.hi))

    def wire(self) -> JsonObject:
        return {'id': self.item_id, 'language': self.language, 'forced': str(self.forced),
                'hi': HI_EXCLUDED if self.hi == 'excluded' else str(self.hi == 'required'),
                'audio_exclude': str(self.audio_exclude), 'audio_only_include': str(self.audio_only_include)}


@dataclass(frozen=True, slots=True)
class ProfileGroup:
    group_id: int
    operator: Literal['AND', 'OR']
    items: tuple[ProfileItem, ...]



@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: int
    name: str
    cutoff: int | None
    profile_type: ProfileType
    items: tuple[ProfileItem, ...]
    groups: tuple[ProfileGroup, ...] | None = None
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    original_format: bool = False
    tag: str | None = None
    desired_languages: frozenset[str] = field(init=False)
    upgrade_languages: frozenset[str] = field(init=False)
    hi_required_languages: frozenset[str] = field(init=False)
    hi_excluded_keys: frozenset[tuple[str, bool]] = field(init=False)
    non_hi_languages: frozenset[str] = field(init=False)
    cutoff_items: tuple[ProfileItem, ...] = field(init=False)

    def __post_init__(self) -> None:
        desired = frozenset(item.language for item in self.items)
        upgrade: set[str] = set()
        for item in self.items:
            if item.forced:
                upgrade.add(f'{item.language}:forced')
            elif item.hi == 'required':
                upgrade.add(f'{item.language}:hi')
            elif item.hi == 'excluded':
                upgrade.add(item.language)
            else:
                upgrade.update((item.language, f'{item.language}:hi'))
        if not self.cutoff:
            cutoff_items = ()
        elif self.cutoff == 65535:
            cutoff_items = self.items
        elif self.groups is None:
            cutoff_items = tuple(item for item in self.items if item.item_id == self.cutoff)
        else:
            cutoff_items = next((group.items for group in self.groups if group.group_id == self.cutoff), ())
        object.__setattr__(self, 'desired_languages', desired)
        object.__setattr__(self, 'upgrade_languages', frozenset(upgrade))
        object.__setattr__(self, 'hi_required_languages', frozenset(item.language for item in self.items if item.hi == 'required'))
        object.__setattr__(self, 'hi_excluded_keys', frozenset((item.language, item.forced) for item in self.items if item.hi == 'excluded'))
        object.__setattr__(self, 'non_hi_languages', frozenset(item.language for item in self.items if not item.forced and item.hi == 'excluded'))
        object.__setattr__(self, 'cutoff_items', cutoff_items)

    def wire_items(self) -> tuple[list[JsonValue], list[JsonValue] | None]:
        payloads = {item: item.wire() for item in self.items}
        items: list[JsonValue] = [payloads[item] for item in self.items]
        groups: list[JsonValue] | None = None
        if self.groups is not None:
            groups = [{'id': group.group_id, 'operator': group.operator,
                       'items': [payloads[item] for item in group.items]} for group in self.groups]
        return items, groups


def _flag(value: JsonValue) -> bool:
    if value is True or value == 'True':
        return True
    if value is False or value == 'False' or value is None:
        return False
    raise ValueError('Invalid profile boolean flag')


def _item(payload: JsonValue) -> ProfileItem:
    if not isinstance(payload, dict):
        raise ValueError('A profile language must be an object')
    item_id, language = payload.get('id'), payload.get('language')
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        raise ValueError('A profile language must have an integer id')
    if not isinstance(language, str) or not language.strip():
        raise ValueError('A profile language must have a language code')
    hi_value = payload.get('hi')
    hi: HiMode = 'excluded' if hi_value == HI_EXCLUDED else ('required' if _flag(hi_value) else 'prefer')
    return ProfileItem(item_id, language, _flag(payload.get('forced')), hi,
                       _flag(payload.get('audio_exclude')), _flag(payload.get('audio_only_include')))


def _items(payload: JsonValue) -> tuple[ProfileItem, ...]:
    if not isinstance(payload, list):
        raise ValueError('Profile languages must be a list')
    return tuple(_item(value) for value in payload)


def parse_profile_items(profile_type: str, raw_items: str) -> tuple[tuple[ProfileItem, ...], tuple[ProfileGroup, ...] | None]:
    payload: JsonValue = json.loads(raw_items)
    if profile_type == 'basic':
        return _items(payload), None
    if profile_type != 'enhanced' or not isinstance(payload, dict):
        raise ValueError('Invalid language profile type or groups')
    raw_groups = payload.get('groups')
    if not isinstance(raw_groups, list):
        raise ValueError('Profile groups must be a list')
    groups: list[ProfileGroup] = []
    for value in raw_groups:
        if not isinstance(value, dict):
            raise ValueError('A profile group must be an object')
        group_id, operator = value.get('id'), value.get('operator')
        if not isinstance(group_id, int) or isinstance(group_id, bool) or operator not in ('AND', 'OR'):
            raise ValueError('Invalid profile group id or operator')
        # The explicit branch narrows the persisted string to the supported operators.
        group = ProfileGroup(group_id, 'AND' if operator == 'AND' else 'OR', _items(value.get('items')))
        groups.append(group)
    return tuple(item for group in groups for item in group.items), tuple(groups)


@dataclass(frozen=True, slots=True)
class ProfileSource:
    profile_id: int
    name: str
    cutoff: int | None
    items: str
    must_contain: str | None
    must_not_contain: str | None
    original_format: int | None
    tag: str | None
    profile_type: str

    def resolve(self) -> Profile:
        items, groups = parse_profile_items(self.profile_type, self.items)
        if self.profile_type not in ('basic', 'enhanced'):
            raise ValueError('Unknown language profile type')
        return Profile(self.profile_id, self.name, self.cutoff,
                       'enhanced' if self.profile_type == 'enhanced' else 'basic', items, groups,
                       tuple(parse_string_list(self.must_contain)) if self.must_contain else (),
                       tuple(parse_string_list(self.must_not_contain)) if self.must_not_contain else (),
                       bool(self.original_format), self.tag)

    def administrative(self, profile: Profile | None, error: str | None = None) -> JsonObject:
        if profile is None:
            # The editor needs arrays even when the stored document is corrupt.
            # Untouched submissions carry these source bytes; applying an edit
            # builds a new profile and drops the preservation metadata.
            result: JsonObject = {
                'profileId': self.profile_id, 'name': self.name, 'cutoff': self.cutoff,
                'profileType': self.profile_type, 'items': [], 'mustContain': [], 'mustNotContain': [],
                'originalFormat': self.original_format, 'tag': self.tag,
                'profileError': error or 'Invalid language profile',
                'profileSource': {'items': self.items, 'mustContain': self.must_contain,
                                   'mustNotContain': self.must_not_contain},
            }
            if self.profile_type == 'enhanced':
                result['groups'] = []
            return result
        items, groups = profile.wire_items()
        result = {
            'profileId': self.profile_id, 'name': self.name, 'cutoff': self.cutoff,
            'profileType': self.profile_type, 'items': items,
            'mustContain': list(profile.must_contain), 'mustNotContain': list(profile.must_not_contain),
            'originalFormat': self.original_format, 'tag': self.tag,
        }
        if groups is not None:
            result['groups'] = groups
        return result


@dataclass(frozen=True, slots=True)
class ProfileCatalog:
    sources: tuple[ProfileSource, ...]
    profiles: Mapping[int, Profile] = field(init=False)
    errors: Mapping[int, str] = field(init=False)

    def __post_init__(self) -> None:
        profiles: dict[int, Profile] = {}
        errors: dict[int, str] = {}
        for source in self.sources:
            try:
                profiles[source.profile_id] = source.resolve()
            except ValueError as error:
                errors[source.profile_id] = str(error)
        object.__setattr__(self, 'profiles', MappingProxyType(profiles))
        object.__setattr__(self, 'errors', MappingProxyType(errors))

    def get(self, profile_id: int | None) -> Profile | None:
        return self.profiles.get(profile_id) if profile_id is not None else None

    def administrative_profiles(self) -> list[JsonObject]:
        return [source.administrative(self.get(source.profile_id), self.errors.get(source.profile_id)) for source in self.sources]


def preserved_profile_sources(payload: JsonValue, catalog: ProfileCatalog) -> dict[int, ProfileSource]:
    if not isinstance(payload, list):
        raise ValueError('Languages profiles must be a list')
    sources = {source.profile_id: source for source in catalog.sources}
    preserved: dict[int, ProfileSource] = {}
    for profile in payload:
        if not isinstance(profile, dict):
            raise ValueError('A languages profile must be an object')
        marker = profile.get('profileSource')
        if marker is None:
            continue
        profile_id = profile.get('profileId')
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            raise ValueError('Invalid profile id')
        source = sources.get(profile_id)
        if source is None or source.administrative(catalog.get(source.profile_id), catalog.errors.get(source.profile_id)).get('profileSource') != marker:
            raise ValueError('The language profile changed. Reload settings before saving.')
        groups = profile.get('groups')
        items = profile.get('items')
        empty = items == [] if source.profile_type != 'enhanced' else items == {'groups': []} and groups in (None, [])
        if not empty or profile.get('mustContain') != [] or profile.get('mustNotContain') != []:
            raise ValueError('Edit the language profile to replace its invalid data before saving.')
        preserved[profile_id] = source
    return preserved


def _optional_integer(value: JsonValue) -> int | None:
    if value in (None, '', 'null', 'undefined'):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError('Profile cutoff must be an integer or empty')


def _strings(value: JsonValue, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f'Profile {field_name} must be a list of strings')
    return [item for item in value if isinstance(item, str)]


def prepare_profile_sources(payload: JsonValue, catalog: ProfileCatalog) -> tuple[ProfileSource, ...]:
    preserved = preserved_profile_sources(payload, catalog)
    if not isinstance(payload, list):
        raise ValueError('Languages profiles must be a list')
    sources: list[ProfileSource] = []
    seen: set[int] = set()
    for profile in payload:
        if not isinstance(profile, dict):
            raise ValueError('A languages profile must be an object')
        profile_id, name = profile.get('profileId'), profile.get('name')
        if not isinstance(profile_id, int) or isinstance(profile_id, bool) or profile_id in seen:
            raise ValueError('Profile ids must be distinct integers')
        seen.add(profile_id)
        if not isinstance(name, str):
            raise ValueError('A language profile must have a name')
        profile_type = profile.get('profileType', 'basic')
        if profile_type not in ('basic', 'enhanced'):
            raise ValueError('Unknown language profile type')
        original = profile.get('originalFormat')
        if original in (None, '', 'null', 'undefined'):
            original_format = None
        elif original is True or original == 1 or original == '1':
            original_format = 1
        elif original is False or original == 0 or original == '0':
            original_format = 0
        else:
            raise ValueError('Invalid original format flag')
        tag = profile.get('tag')
        if tag is not None and not isinstance(tag, str):
            raise ValueError('Profile tag must be a string or empty')
        cutoff = _optional_integer(profile.get('cutoff'))
        previous = preserved.get(profile_id)
        if previous is not None:
            if previous.profile_type != profile_type:
                raise ValueError('Edit the language profile before changing its type')
            sources.append(ProfileSource(profile_id, name, cutoff, previous.items, previous.must_contain,
                                          previous.must_not_contain, original_format, tag, previous.profile_type))
            continue
        items = profile.get('items')
        parsed_items, groups = parse_profile_items(str(profile_type), json.dumps(items))
        if groups is not None:
            group_ids = [group.group_id for group in groups]
            item_ids = [item.item_id for item in parsed_items]
            if not groups or any(not group.items for group in groups):
                raise ValueError('An enhanced profile must have groups containing languages')
            if 65535 in group_ids or len(set(group_ids)) != len(group_ids) or len(set(item_ids)) != len(item_ids):
                raise ValueError('Enhanced profile group and language ids must be distinct and valid')
            if cutoff is not None and cutoff != 65535 and cutoff not in group_ids:
                raise ValueError('Profile cutoff does not match a group')
        sources.append(ProfileSource(profile_id, name, cutoff, json.dumps(items),
                                      str(_strings(profile.get('mustContain'), 'mustContain')),
                                      str(_strings(profile.get('mustNotContain'), 'mustNotContain')),
                                      original_format, tag, str(profile_type)))
    return tuple(sources)
