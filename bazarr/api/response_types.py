# coding=utf-8

from datetime import datetime
from typing import TypeAlias, TypedDict
from typing_extensions import NotRequired


JsonValue: TypeAlias = str | int | float | bool | None | list['JsonValue'] | dict[str, 'JsonValue']
JsonObject: TypeAlias = dict[str, JsonValue]


class MediaMetadata(TypedDict, total=False):
    action: int | None
    blacklisted: bool
    created_at_timestamp: datetime | None
    description: str | None
    dont_matches: str | list[str] | None
    ended: str | None
    episode: int
    episodeFileCount: int | None
    episodeMissingCount: int | None
    episodeTitle: str | None
    episode_number: str | None
    external_subtitles: str | None
    fanart: str | None
    ffprobe_cache: bytes | None
    id: int
    imdbId: str | None
    lastAired: str | None
    matches: str | list[str] | None
    overview: str | None
    parsed_timestamp: str
    path: str | None
    poster: str | None
    profileId: int | str | None
    provider: str | None
    radarrId: int | None
    sceneName: str | None
    score: int | str | None
    score_out_of: int | None
    season: int
    seriesTitle: str | None
    seriesType: str | None
    sonarrEpisodeId: int | None
    sonarrSeriesId: int | None
    subs_id: str | None
    subtitles_path: str | None
    timestamp: datetime | str | None
    title: str | None
    tvdbId: int | None
    video_path: str | None
    year: str | None


class MediaRow(MediaMetadata, total=False):
    alternativeTitles: str | None
    audio_language: str | None
    hearing_impaired: str | None
    language: str | None
    missing_subtitles: str | list[str] | None
    monitored: str | None
    tags: str | None


class LanguageInfo(TypedDict):
    name: str | None
    code2: str | None
    code3: str | None


class SubtitleLanguage(LanguageInfo):
    forced: bool
    hi: bool


class MediaResponse(MediaMetadata):
    alternativeTitles: list[str | None]
    audio_language: NotRequired[str | list[LanguageInfo] | None]
    hearing_impaired: bool
    language: NotRequired[str | SubtitleLanguage | None]
    missing_subtitles: list[SubtitleLanguage]
    monitored: bool
    original_id: NotRequired[int | None]
    subtitles: list[JsonObject]
    tags: list[str | None]
    upgradable: NotRequired[bool]
