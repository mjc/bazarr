# coding=utf-8

import os
import re
from datetime import datetime

from dateutil import parser

from sonarr.series_snapshot import Series, SeriesValues
from utilities.arr_types import KEEP_PROFILE, ProfileAssignment
from utilities.media_codecs import sonarr_codecs
from sonarr.episode_snapshot import Episode, EpisodeFile, EpisodeValues


_LAST_AIRED_TIMESTAMP = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
    r'(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?'
)


def _parse_last_aired(value: str) -> datetime:
    if _LAST_AIRED_TIMESTAMP.fullmatch(value):
        try:
            # Python 3.10 requires an explicit UTC offset instead of Z.
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            pass
    aired = parser.parse(value)
    if not isinstance(aired, datetime):
        raise ValueError('Invalid last-aired date')
    return aired


def map_series(show: Series, *, audio_language: str, tags: list[str],
               profile_id: ProfileAssignment = KEEP_PROFILE) -> SeriesValues:
    overview = show.overview or ''
    poster = ''
    fanart = ''
    for image in show.images:
        if image.coverType == 'poster':
            poster_big = image.url.split('?')[0]
            stem, extension = os.path.splitext(poster_big)
            poster = f'{stem}-250{extension}'

        if image.coverType == 'fanart':
            fanart = image.url.split('?')[0]

    alternate_titles = [item.title for item in show.alternateTitles or ()
                        if item.title not in (None, '', show.title)]

    lastAired = None
    if show.lastAired:
        lastAired = _parse_last_aired(show.lastAired).strftime('%Y-%m-%d')

    original = show.originalLanguage

    parsed_series: SeriesValues = {
        'title': show.title,
        'path': show.path,
        'tvdbId': show.tvdbId,
        'sonarrSeriesId': show.id,
        'overview': overview,
        'poster': poster,
        'fanart': fanart,
        'audio_language': audio_language,
        'sortTitle': show.sortTitle,
        'year': str(show.year),
        'alternativeTitles': str(alternate_titles),
        'tags': str(tags),
        'seriesType': show.seriesType,
        'imdbId': show.imdbId,
        'tmdbId': show.tmdbId or None,
        'monitored': str(show.monitored),
        'ended': str(show.ended),
        'lastAired': lastAired,
        'originalLanguage': original.name if original is not None else None,
    }

    if profile_id is not KEEP_PROFILE:
        parsed_series['profileId'] = profile_id
    return parsed_series


def map_episode(episode: Episode, file: EpisodeFile, *, audio_language: str) -> EpisodeValues:
    media_info = file.mediaInfo
    audio_codec, video_codec = sonarr_codecs(media_info.audioCodec, media_info.videoCodec) if media_info else (None, None)
    quality = file.quality.quality
    try:
        video_format, video_resolution = quality.name.split('-')
    except ValueError:
        video_format = quality.name
        resolution = quality.resolution
        video_resolution = f'{resolution}p' if resolution is not None else None
    return {'sonarrSeriesId': episode.seriesId,
            'sonarrEpisodeId': episode.id,
            'title': episode.title,
            'path': file.path,
            'season': episode.seasonNumber,
            'episode': episode.episodeNumber,
            'sceneName': file.sceneName,
            'monitored': str(episode.monitored),
            'format': video_format,
            'resolution': video_resolution,
            'video_codec': video_codec,
            'audio_codec': audio_codec,
            'episode_file_id': file.id,
            'audio_language': audio_language,
            'file_size': file.size,
            'absoluteEpisode': episode.absoluteEpisodeNumber,
            'tvdbId': episode.tvdbId}
