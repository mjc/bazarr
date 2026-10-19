# coding=utf-8

from typing import TypeGuard, TypedDict
from typing_extensions import NotRequired

from utilities.json_types import JsonValue


class Language(TypedDict):
    name: str | None


class Quality(TypedDict):
    name: str
    resolution: NotRequired[int]


class FileQuality(TypedDict):
    quality: Quality


class ArrTag(TypedDict):
    id: int
    label: str


def is_arr_tags(value: JsonValue) -> TypeGuard[list[ArrTag]]:
    return isinstance(value, list) and all(
        isinstance(tag, dict) and type(tag.get('id')) is int and isinstance(tag.get('label'), str)
        for tag in value
    )
