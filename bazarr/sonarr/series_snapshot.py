# coding=utf-8

from dataclasses import dataclass
from datetime import datetime
from typing import TypeGuard, TypedDict
from typing_extensions import NotRequired

from utilities.json_types import JsonValue


class SeriesTag(TypedDict):
    id: int
    label: str


def is_series_tags(value: JsonValue) -> TypeGuard[list[SeriesTag]]:
    return isinstance(value, list) and all(
        isinstance(tag, dict) and type(tag.get('id')) is int and isinstance(tag.get('label'), str)
        for tag in value
    )


class SeriesImage(TypedDict):
    coverType: str
    url: str


class AlternateTitle(TypedDict):
    title: NotRequired[str | None]


class SeriesLanguage(TypedDict):
    name: NotRequired[str | None]


class Series(TypedDict):
    id: int
    title: str
    path: str
    tvdbId: int
    sortTitle: str
    year: int | str
    seriesType: str
    monitored: bool
    images: list[SeriesImage]
    alternateTitles: list[AlternateTitle] | None
    tags: list[int]
    overview: NotRequired[str | None]
    imdbId: NotRequired[str | None]
    ended: NotRequired[bool]
    lastAired: NotRequired[str | None]
    originalLanguage: NotRequired[SeriesLanguage | None]
    qualityProfileId: NotRequired[int]
    languageProfileId: NotRequired[int]


class SeriesValues(TypedDict):
    title: str
    path: str
    tvdbId: int
    sonarrSeriesId: int
    overview: str | None
    poster: str
    fanart: str
    audio_language: str
    sortTitle: str
    year: str
    alternativeTitles: str
    tags: str
    seriesType: str
    imdbId: str | None
    monitored: str
    ended: str
    lastAired: str | None
    originalLanguage: str | None
    profileId: NotRequired[int | None]
    updated_at_timestamp: NotRequired[datetime]
    created_at_timestamp: NotRequired[datetime]


@dataclass(frozen=True)
class SeriesSyncPlan:
    updates: list[SeriesValues]
    retained_ids: set[int]
    existing_ids: set[int]
    complete: bool
    series_count: int
    skipped_count: int


def is_series(value: JsonValue) -> TypeGuard[Series]:
    if not isinstance(value, dict):
        return False
    for key in ('id', 'tvdbId'):
        if type(value.get(key)) is not int:
            return False
    for key in ('title', 'path', 'sortTitle', 'seriesType'):
        if not isinstance(value.get(key), str):
            return False
    if not isinstance(value.get('year'), (str, int)) or not isinstance(value.get('monitored'), bool):
        return False
    images = value.get('images')
    if not isinstance(images, list):
        return False
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get('coverType'), str) or not isinstance(image.get('url'), str):
            return False
    if 'alternateTitles' not in value:
        return False
    titles = value['alternateTitles']
    if titles is not None:
        if not isinstance(titles, list):
            return False
        for title in titles:
            if not isinstance(title, dict):
                return False
            if 'title' in title and title['title'] is not None and not isinstance(title['title'], str):
                return False
    tags = value.get('tags')
    if not isinstance(tags, list) or not all(type(tag) is int for tag in tags):
        return False
    for key in ('overview', 'imdbId', 'lastAired'):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            return False
    if 'ended' in value and not isinstance(value['ended'], bool):
        return False
    for key in ('qualityProfileId', 'languageProfileId'):
        if key in value and type(value[key]) is not int:
            return False
    language = value.get('originalLanguage')
    if language is not None:
        if not isinstance(language, dict):
            return False
        if 'name' in language and language['name'] is not None and not isinstance(language['name'], str):
            return False
    return True
