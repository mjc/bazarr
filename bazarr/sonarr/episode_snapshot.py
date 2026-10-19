# coding=utf-8

from dataclasses import dataclass
from datetime import datetime
from typing import TypeGuard, TypedDict
from typing_extensions import NotRequired

from utilities.json_types import JsonValue
from utilities.arr_types import Language, FileQuality


class MediaInfo(TypedDict):
    videoCodec: NotRequired[str | None]
    audioCodec: NotRequired[str | None]


class EpisodeFile(TypedDict):
    id: int
    path: str
    size: int
    quality: FileQuality
    sceneName: NotRequired[str | None]
    language: NotRequired[Language | None]
    languages: NotRequired[list[Language] | None]
    mediaInfo: NotRequired[MediaInfo | None]


class Episode(TypedDict):
    id: int
    seriesId: int
    title: str
    hasFile: bool
    monitored: bool
    seasonNumber: int
    episodeNumber: int
    episodeFileId: NotRequired[int]
    episodeFile: NotRequired[EpisodeFile | None]
    absoluteEpisodeNumber: NotRequired[int | None]
    tvdbId: NotRequired[int | None]


class EpisodeValues(TypedDict):
    sonarrSeriesId: int
    sonarrEpisodeId: int
    title: str
    path: str
    season: int
    episode: int
    sceneName: str | None
    monitored: str
    format: str
    resolution: str | None
    video_codec: str | None
    audio_codec: str | None
    episode_file_id: int
    audio_language: str
    file_size: int
    absoluteEpisode: int | None
    tvdbId: int | None
    created_at_timestamp: NotRequired[datetime]
    updated_at_timestamp: NotRequired[datetime]


@dataclass(frozen=True)
class EpisodeSnapshot:
    episodes: list[Episode]


@dataclass(frozen=True)
class EpisodeSyncPlan:
    deleted_ids: list[int]
    additions: list[EpisodeValues]
    updates: list[EpisodeValues]
    episode_count: int
    skipped_count: int


def _language(value: JsonValue) -> bool:
    return isinstance(value, dict) and 'name' in value and (value['name'] is None or isinstance(value['name'], str))


def is_episode_file(value: JsonValue) -> TypeGuard[EpisodeFile]:
    if not isinstance(value, dict):
        return False
    file_id, path, size = value.get('id'), value.get('path'), value.get('size')
    if not (type(file_id) is int and file_id > 0
            and isinstance(path, str) and path.strip()
            and type(size) is int and size >= 0):
        return False
    quality = value.get('quality')
    if not isinstance(quality, dict):
        return False
    definition = quality.get('quality')
    if not isinstance(definition, dict) or not isinstance(definition.get('name'), str):
        return False
    if 'resolution' in definition and type(definition['resolution']) is not int:
        return False
    if 'sceneName' in value and value['sceneName'] is not None and not isinstance(value['sceneName'], str):
        return False
    if 'language' in value and value['language'] is not None and not _language(value['language']):
        return False
    if 'languages' in value and value['languages'] is not None:
        languages = value['languages']
        if not isinstance(languages, list) or not all(_language(language) for language in languages):
            return False
    if 'mediaInfo' in value and value['mediaInfo'] is not None:
        media = value['mediaInfo']
        if not isinstance(media, dict):
            return False
        for key in ('videoCodec', 'audioCodec'):
            if key in media and media[key] is not None and not isinstance(media[key], str):
                return False
    return True


def is_episode(value: JsonValue) -> TypeGuard[Episode]:
    if not isinstance(value, dict):
        return False
    for key in ('id', 'seriesId', 'seasonNumber', 'episodeNumber'):
        if type(value.get(key)) is not int:
            return False
    episode_id, series_id = value['id'], value['seriesId']
    if type(episode_id) is not int or type(series_id) is not int or episode_id <= 0 or series_id <= 0:
        return False
    if not isinstance(value.get('title'), str):
        return False
    if not isinstance(value.get('hasFile'), bool) or not isinstance(value.get('monitored'), bool):
        return False
    for key in ('absoluteEpisodeNumber', 'tvdbId'):
        if key in value and value[key] is not None and type(value[key]) is not int:
            return False
    if 'episodeFileId' in value and type(value['episodeFileId']) is not int:
        return False
    if 'episodeFile' in value and value['episodeFile'] is not None and not is_episode_file(value['episodeFile']):
        return False
    return True


def _episode_list(value: JsonValue) -> TypeGuard[list[Episode]]:
    return isinstance(value, list) and all(is_episode(episode) for episode in value)


def complete_episode_snapshot(episodes: JsonValue, episode_files: JsonValue = None,
                              *, separate_files: bool = False) -> EpisodeSnapshot | None:
    if not _episode_list(episodes):
        return None
    files_by_id: dict[int, EpisodeFile] = {}
    if separate_files:
        if not isinstance(episode_files, list):
            return None
        for file in episode_files:
            if not is_episode_file(file) or file['id'] in files_by_id:
                return None
            files_by_id[file['id']] = file
    seen_ids: set[int] = set()
    for episode in episodes:
        if episode['id'] in seen_ids:
            return None
        seen_ids.add(episode['id'])
        if not episode['hasFile']:
            continue
        if separate_files:
            file_id = episode.get('episodeFileId')
            if file_id is None or file_id not in files_by_id:
                return None
        elif episode.get('episodeFile') is None:
            return None
    if separate_files:
        for episode in episodes:
            if episode['hasFile']:
                file_id = episode.get('episodeFileId')
                if file_id is None:
                    return None
                episode['episodeFile'] = files_by_id[file_id]
    return EpisodeSnapshot(episodes)
