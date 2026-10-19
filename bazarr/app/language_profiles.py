# coding=utf-8
"""Language profile records shared by database lookups and subtitle consumers."""
import json
from typing import Literal, TypedDict
from typing_extensions import NotRequired


class ProfileItem(TypedDict):
    language: str
    id: int
    forced: NotRequired[str | bool]
    hi: NotRequired[str | bool]
    audio_exclude: NotRequired[str | bool]
    audio_only_include: NotRequired[str | bool]


class ProfileGroup(TypedDict):
    id: int
    operator: Literal['AND', 'OR']
    items: list[ProfileItem]


class Profile(TypedDict):
    profileId: int
    name: str
    cutoff: int | None
    profileType: Literal['basic', 'enhanced']
    items: list[ProfileItem]
    groups: NotRequired[list[ProfileGroup]]
    mustContain: list[str]
    mustNotContain: list[str]
    originalFormat: int | None
    tag: str | None


def _parse_item(payload: object) -> ProfileItem | None:
    if not isinstance(payload, dict):
        return None
    item_id: object = payload.get('id')
    language: object = payload.get('language')
    if not isinstance(item_id, int) or isinstance(item_id, bool) or not isinstance(language, str):
        return None
    item: ProfileItem = {'id': item_id, 'language': language}
    for key in ('forced', 'hi', 'audio_exclude', 'audio_only_include'):
        if key in payload:
            value: object = payload[key]
            if not isinstance(value, (str, bool)):
                return None
            item[key] = value
    return item


def _parse_items(payload: object) -> list[ProfileItem] | None:
    if not isinstance(payload, list):
        return None
    items: list[ProfileItem] = []
    for raw in payload:
        item = _parse_item(raw)
        if item is None:
            return None
        items.append(item)
    return items


def parse_profile_items(profile_type: str, raw_items: str | None) -> tuple[list[ProfileItem], list[ProfileGroup] | None] | None:
    if raw_items is None:
        return None
    try:
        data: object = json.loads(raw_items)
    except json.JSONDecodeError:
        return None
    if profile_type == 'enhanced':
        if not isinstance(data, dict):
            return None
        raw_groups: object = data.get('groups')
        if not isinstance(raw_groups, list):
            return None
        groups: list[ProfileGroup] = []
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                return None
            group_id: object = raw_group.get('id')
            operator: object = raw_group.get('operator')
            group_items = _parse_items(raw_group.get('items'))
            if not isinstance(group_id, int) or isinstance(group_id, bool):
                return None
            if operator not in ('AND', 'OR') or group_items is None:
                return None
            groups.append({'id': group_id, 'operator': operator, 'items': group_items})
        return [item for group in groups for item in group['items']], groups
    items = _parse_items(data)
    return None if items is None else (items, None)
