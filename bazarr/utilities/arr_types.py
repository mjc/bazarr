# coding=utf-8

from dataclasses import dataclass
from enum import Enum, auto
import re
from typing import Annotated, Literal, TypeAlias
from typing_extensions import TypedDict

from pydantic import BeforeValidator, Field, StrictInt, StrictStr, TypeAdapter

from utilities.json_types import JsonValue


def _present_integer(value: JsonValue) -> JsonValue:
    if value is None:
        raise ValueError('Expected an integer, got null')
    return value


# Omitted fields default to None; explicitly supplied nulls remain invalid.
OmittedInt: TypeAlias = Annotated[
    StrictInt | Annotated[None, BeforeValidator(_present_integer)], Field(union_mode='left_to_right')]


class _KeepProfile(Enum):
    KEEP = auto()


KEEP_PROFILE = _KeepProfile.KEEP
ProfileAssignment: TypeAlias = int | None | _KeepProfile

ProfileId: TypeAlias = int | float | str | bool
AudioProfile: TypeAlias = tuple[ProfileId, str]
WriteOutcome: TypeAlias = Literal['failed', 'updated', 'reindexed']


@dataclass(slots=True)
class LegacyAudioProfile:
    id: ProfileId
    language: str


@dataclass(slots=True)
class SonarrAudioProfile:
    id: ProfileId
    name: str


@dataclass(slots=True)
class ProfileLanguage:
    name: str


@dataclass(slots=True)
class RadarrAudioProfile:
    id: ProfileId
    language: ProfileLanguage


@dataclass(slots=True)
class Language:
    name: str | None


@dataclass(slots=True)
class Quality:
    name: str
    resolution: OmittedInt = None


@dataclass(slots=True)
class FileQuality:
    quality: Quality


class ArrTag(TypedDict):
    id: int
    label: str


_JSON_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_YEAR_NUMBER = TypeAdapter[int](int)


def _year_input(value: JsonValue) -> JsonValue:
    if isinstance(value, bool):
        raise ValueError('Year must be an integer')
    if isinstance(value, str):
        if not _JSON_NUMBER.fullmatch(value):
            raise ValueError('Year must be a JSON number')
        return _YEAR_NUMBER.validate_json(value)
    return value


_FILE_SIZE_TYPES = (int, str, float)


def parse_file_size(value: JsonValue) -> int | None:
    if isinstance(value, bool) or not isinstance(value, _FILE_SIZE_TYPES):
        return None
    try:
        return int(value)
    except (ValueError, OverflowError):
        return None


DisplayText: TypeAlias = Annotated[
    StrictStr | Annotated[str, BeforeValidator(str)], Field(union_mode='left_to_right')]
Year: TypeAlias = Annotated[
    StrictInt | Annotated[int, Field(strict=False), BeforeValidator(_year_input)], Field(union_mode='left_to_right')]
RadarrFileSizeBytes: TypeAlias = Annotated[
    StrictInt | Annotated[int, BeforeValidator(parse_file_size)], Field(union_mode='left_to_right')]
