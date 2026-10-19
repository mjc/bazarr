# coding=utf-8

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, TypeAlias, TypedDict

from languages.formatting import LanguageInfo, SubtitleLanguage
from utilities.subtitle_metadata import SubtitleFile

JsonValue: TypeAlias = str | int | float | bool | None | list['JsonValue'] | dict[str, 'JsonValue']
JsonObject: TypeAlias = dict[str, JsonValue]
MarshalledRow: TypeAlias = Mapping[str, JsonValue]
AudioValue: TypeAlias = list[LanguageInfo] | Literal[''] | None
LanguageValue: TypeAlias = SubtitleLanguage | Literal[''] | None


class ListResponse(TypedDict):
    data: list[MarshalledRow]


class PageResponse(ListResponse):
    total: int | None


class MediaMetadata(TypedDict, total=False):
    action: int | None
    blacklisted: bool
    created_at_timestamp: datetime | None
    description: str | None
    ended: str | None
    episode: int
    episodeFileCount: int | None
    episodeMissingCount: int | None
    episodeTitle: str | None
    episode_number: str | None
    fanart: str | None
    imdbId: str | None
    lastAired: str | None
    overview: str | None
    path: str | None
    poster: str | None
    profileId: int | None
    provider: str | None
    radarrId: int | None
    sceneName: str | None
    season: int
    seriesTitle: str | None
    seriesType: str | None
    sonarrEpisodeId: int | None
    sonarrSeriesId: int | None
    subs_id: str | None
    subtitles_path: str | None
    title: str | None
    tvdbId: int | None
    year: str | None


class SeriesRow(MediaMetadata):
    alternativeTitles: str | None
    audio_language: str | None
    monitored: str | None
    tags: str | None


class EpisodeRow(MediaMetadata):
    audio_language: str | None
    missing_subtitles: list[str]
    monitored: str | None


class MovieRow(SeriesRow):
    missing_subtitles: list[str]


class WantedRow(MediaMetadata):
    missing_subtitles: list[str]
    tags: str | None


class BlacklistRow(MediaMetadata):
    timestamp: datetime
    language: str | None


class HistoryRow(MediaMetadata):
    id: int
    timestamp: datetime | None
    language: str | None
    score: int | None
    score_out_of: int | None
    monitored: str | None
    tags: str | None
    video_path: str | None
    external_subtitles: str | None
    matched: str | None
    not_matched: str | None


class SeriesResponse(MediaMetadata):
    alternativeTitles: list[str | None]
    audio_language: AudioValue
    monitored: bool
    tags: list[str | None]


class EpisodeResponse(MediaMetadata):
    audio_language: AudioValue
    missing_subtitles: list[SubtitleLanguage]
    monitored: bool
    subtitles: list[SubtitleFile]


class MovieResponse(SeriesResponse):
    missing_subtitles: list[SubtitleLanguage]
    subtitles: list[SubtitleFile]


class WantedResponse(MediaMetadata):
    missing_subtitles: list[SubtitleLanguage]
    tags: list[str | None]


class BlacklistResponse(MediaMetadata):
    timestamp: str
    parsed_timestamp: str
    language: LanguageValue


class HistoryResponse(MediaMetadata):
    timestamp: str | None
    parsed_timestamp: str | None
    language: LanguageValue
    score: str | None
    monitored: bool
    tags: list[str | None]
    matches: list[str]
    dont_matches: list[str]
    upgradable: bool
