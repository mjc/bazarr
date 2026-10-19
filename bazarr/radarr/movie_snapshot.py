# coding=utf-8

from dataclasses import dataclass
from datetime import datetime
from typing import TypeGuard, TypedDict
from typing_extensions import NotRequired

from utilities.arr_types import Language, FileQuality
from utilities.json_types import JsonValue


class MovieMediaInfo(TypedDict):
    videoFormat: NotRequired[str | None]
    videoCodec: NotRequired[str | None]
    videoCodecID: NotRequired[str | None]
    videoCodecLibrary: NotRequired[str | None]
    audioFormat: NotRequired[str | None]
    audioCodec: NotRequired[str | None]
    audioCodecID: NotRequired[str | None]
    audioProfile: NotRequired[str | None]
    audioAdditionalFeatures: NotRequired[str | None]
    audioLanguages: NotRequired[str | None]


class MovieFile(TypedDict):
    id: int
    path: str
    size: int | str | float
    quality: FileQuality
    sceneName: NotRequired[str | None]
    languages: NotRequired[list[Language] | None]
    mediaInfo: NotRequired[MovieMediaInfo | None]


class MovieImage(TypedDict):
    url: str


class MovieTitle(TypedDict):
    title: str | None


class MovieLanguage(TypedDict):
    name: NotRequired[str | None]


class Movie(TypedDict):
    id: int
    title: str
    tmdbId: int | str
    year: int | str
    sortTitle: str
    monitored: bool
    hasFile: bool
    movieFile: MovieFile
    tags: list[int]
    images: NotRequired[list[MovieImage] | None]
    overview: NotRequired[JsonValue]
    alternativeTitles: NotRequired[list[MovieTitle] | None]
    alternateTitles: NotRequired[list[MovieTitle] | None]
    imdbId: NotRequired[str | None]
    originalLanguage: NotRequired[MovieLanguage | None]
    qualityProfileId: NotRequired[int]


class MovieValues(TypedDict):
    radarrId: int
    title: str
    path: str
    tmdbId: str
    poster: str
    fanart: str
    audio_language: str
    sceneName: str | None
    monitored: str
    year: str
    sortTitle: str
    alternativeTitles: str | None
    format: str
    resolution: str | None
    video_codec: str | None
    audio_codec: str | None
    overview: str
    imdbId: str | None
    movie_file_id: int
    tags: str
    file_size: int
    originalLanguage: str | None
    profileId: NotRequired[int | None]
    created_at_timestamp: NotRequired[datetime]
    updated_at_timestamp: NotRequired[datetime]


@dataclass(frozen=True)
class MovieChange:
    values: MovieValues
    existing: bool
    position: int


@dataclass(frozen=True)
class MovieSyncPlan:
    changes: list[MovieChange]
    deleted_ids: list[int]
    movie_count: int
    missing_count: int
    skipped_count: int


def movie_file_size(value: JsonValue) -> int | None:
    if not isinstance(value, (int, str, float)) or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, OverflowError):
        return None


def is_movie(value: JsonValue) -> TypeGuard[Movie]:
    if not isinstance(value, dict) or type(value.get('id')) is not int:
        return False
    if not isinstance(value.get('title'), str) or not isinstance(value.get('sortTitle'), str):
        return False
    if not isinstance(value.get('tmdbId'), (str, int)) or not isinstance(value.get('year'), (str, int)):
        return False
    if not isinstance(value.get('monitored'), bool) or not isinstance(value.get('hasFile'), bool):
        return False
    file = value.get('movieFile')
    if not isinstance(file, dict) or type(file.get('id')) is not int or not isinstance(file.get('path'), str):
        return False
    if movie_file_size(file.get('size')) is None:
        return False
    quality = file.get('quality')
    definition = quality.get('quality') if isinstance(quality, dict) else None
    if not isinstance(definition, dict) or not isinstance(definition.get('name'), str):
        return False
    if 'resolution' in definition and type(definition['resolution']) is not int:
        return False
    for key in ('sceneName',):
        if key in file and file[key] is not None and not isinstance(file[key], str):
            return False
    languages = file.get('languages')
    if languages is not None:
        if not isinstance(languages, list):
            return False
        for language in languages:
            if not isinstance(language, dict) or 'name' not in language:
                return False
            if language['name'] is not None and not isinstance(language['name'], str):
                return False
    media = file.get('mediaInfo')
    if media is not None:
        if not isinstance(media, dict):
            return False
        for key in ('videoFormat', 'videoCodec', 'videoCodecID', 'videoCodecLibrary', 'audioFormat',
                    'audioCodec', 'audioCodecID', 'audioProfile', 'audioAdditionalFeatures', 'audioLanguages'):
            if key in media and media[key] is not None and not isinstance(media[key], str):
                return False
    tags = value.get('tags')
    if not isinstance(tags, list) or any(type(tag) is not int for tag in tags):
        return False
    images = value.get('images')
    if images is not None:
        if not isinstance(images, list) or any(not isinstance(image, dict) or not isinstance(image.get('url'), str)
                                             for image in images):
            return False
    for key in ('alternateTitles', 'alternativeTitles'):
        titles = value.get(key)
        if titles is not None:
            if not isinstance(titles, list):
                return False
            for title in titles:
                if not isinstance(title, dict) or 'title' not in title:
                    return False
                if title['title'] is not None and not isinstance(title['title'], str):
                    return False
    if 'imdbId' in value and value['imdbId'] is not None and not isinstance(value['imdbId'], str):
        return False
    language = value.get('originalLanguage')
    if language is not None:
        if not isinstance(language, dict):
            return False
        if 'name' in language and language['name'] is not None and not isinstance(language['name'], str):
            return False
    if 'qualityProfileId' in value and type(value['qualityProfileId']) is not int:
        return False
    return True
