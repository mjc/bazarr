# coding=utf-8

from collections.abc import Sequence
from datetime import datetime
from typing import TypeAlias, TypedDict

from languages.formatting import LanguageInfo, SubtitleLanguage
from utilities.subtitle_metadata import SubtitleFile
from utilities.json_types import JsonObject, JsonValue


class EmptySubtitleLanguage(LanguageInfo):
    forced: None
    hi: None


AudioValue: TypeAlias = list[LanguageInfo] | LanguageInfo
LanguageValue: TypeAlias = SubtitleLanguage | EmptySubtitleLanguage


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
    missing_subtitles: Sequence[str]
    monitored: str | None


class MovieRow(SeriesRow):
    missing_subtitles: Sequence[str]


class WantedRow(MediaMetadata):
    missing_subtitles: Sequence[str]
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


class MediaResponse(TypedDict):
    alternativeTitles: list[str | None]
    audio_language: AudioValue
    created_at_timestamp: str | None
    fanart: str | None
    imdbId: str | None
    monitored: bool
    overview: str | None
    path: str | None
    poster: str | None
    profileId: int | None
    tags: list[str | None]
    title: str | None
    year: str | None


class SeriesResponse(MediaResponse):
    episodeFileCount: int
    ended: bool | None
    episodeMissingCount: int
    lastAired: str | None
    seriesType: str | None
    sonarrSeriesId: int | None
    tvdbId: int | None


class EpisodeResponse(TypedDict):
    audio_language: AudioValue
    episode: int | None
    missing_subtitles: list[SubtitleLanguage]
    monitored: bool
    path: str | None
    season: int | None
    sonarrEpisodeId: int | None
    sonarrSeriesId: int | None
    subtitles: list[SubtitleFile]
    title: str | None
    sceneName: str | None


class MovieResponse(MediaResponse):
    missing_subtitles: list[SubtitleLanguage]
    radarrId: int | None
    sceneName: str | None
    subtitles: list[SubtitleFile]


class WantedResponse(MediaMetadata):
    missing_subtitles: list[SubtitleLanguage]
    tags: list[str | None]


class WantedPageResponse(TypedDict):
    data: list[WantedResponse]
    total: int | None


class TagResponse(TypedDict):
    tag: str


class TagsResponse(TypedDict):
    data: list[TagResponse]


class FileBrowserEntry(TypedDict):
    name: str | None
    children: bool
    path: str | None


class BlacklistIdentity(TypedDict, total=False):
    seriesTitle: str | None
    episode_number: str | None
    episodeTitle: str | None
    sonarrSeriesId: int | None
    title: str | None
    radarrId: int | None


class BlacklistResponse(BlacklistIdentity):
    provider: str | None
    subs_id: str | None
    timestamp: str
    parsed_timestamp: str
    language: LanguageValue


class HistoryIdentity(TypedDict, total=False):
    seriesTitle: str | None
    episode_number: str | None
    episodeTitle: str | None
    sonarrSeriesId: int | None
    sonarrEpisodeId: int | None
    title: str | None
    radarrId: int | None
    path: None


class HistoryResponse(HistoryIdentity):
    action: int | None
    description: str | None
    subs_id: str | None
    provider: str | None
    blacklisted: bool
    subtitles_path: str | None
    timestamp: str | None
    parsed_timestamp: str | None
    language: LanguageValue
    score: str | None
    monitored: bool
    tags: list[str | None]
    matches: list[str]
    dont_matches: list[str]
    upgradable: bool


class SeriesPageResponse(TypedDict):
    data: list[SeriesResponse]
    total: int | None


class MoviesPageResponse(TypedDict):
    data: list[MovieResponse]
    total: int | None


class EpisodesResponse(TypedDict):
    data: list[EpisodeResponse]


class HistoryPageResponse(TypedDict):
    data: list[HistoryResponse]
    total: int | None


class BlacklistPageResponse(TypedDict):
    data: list[BlacklistResponse]


class BadgesResponse(TypedDict):
    episodes: int | None
    movies: int | None
    providers: int
    status: int
    sonarr_signalr: str
    radarr_signalr: str
    announcements: int


class TaskResponse(TypedDict):
    interval: str
    job_id: str
    job_running: bool
    name: str
    next_run_in: str
    next_run_time: str


class TasksResponse(TypedDict):
    data: list[TaskResponse]


class LogEntry(TypedDict):
    timestamp: str | None
    type: str
    message: str
    exception: str | None


class LogsResponse(TypedDict):
    data: list[LogEntry]


class ProviderStatus(TypedDict):
    name: str | None
    status: str
    retry: str


class ProvidersStatusResponse(TypedDict):
    data: list[ProviderStatus]


class SubtitleNameResult(TypedDict):
    filename: str
    subtitle_language: str | None
    season: int | None
    episode: int | None


class SubtitleNamesResponse(TypedDict):
    data: list[SubtitleNameResult]


class ReleaseInfo(TypedDict):
    body: list[str | None] | None
    name: str | None
    date: str | None
    prerelease: bool | None
    current: bool | None


class ReleasesResponse(TypedDict):
    data: list[ReleaseInfo]
