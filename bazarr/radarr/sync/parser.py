# coding=utf-8

import os

from radarr.movie_snapshot import Movie, MovieFile, MovieValues
from utilities.arr_types import KEEP_PROFILE, ProfileAssignment
from utilities.media_codecs import radarr_audio_codec, radarr_video_codec


def map_movie(movie: Movie, file: MovieFile, *,
              audio_language: str, tags: list[str],
              profile_id: ProfileAssignment = KEEP_PROFILE) -> MovieValues:
    images = movie.images or ()
    poster = ''
    if images:
        poster_big = images[0].url
        stem, extension = os.path.splitext(poster_big)
        poster = f'{stem}-500{extension}'
    fanart = images[1].url if len(images) > 1 else ''
    titles = movie.alternateTitles
    alternative_titles = str([item.title for item in titles]) if titles is not None else None
    quality = file.quality.quality
    try:
        video_format, resolution = quality.name.split('-')
    except ValueError:
        video_format = quality.name
        quality_resolution = quality.resolution
        resolution = f'{quality_resolution}p' if quality_resolution is not None else None
    media = file.mediaInfo
    if media:
        video_codec = radarr_video_codec(
            media.videoCodec, media.videoCodecID, media.videoCodecLibrary)
        audio_codec = radarr_audio_codec(
            media.audioCodec, media.audioCodecID, media.audioProfile,
            media.audioAdditionalFeatures)
    else:
        video_codec = audio_codec = None
    original = movie.originalLanguage
    parsed: MovieValues = {'radarrId': movie.id,
                          'title': movie.title,
                          'path': file.path,
                          'tmdbId': str(movie.tmdbId),
                          'poster': poster,
                          'fanart': fanart,
                          'audio_language': audio_language,
                          'sceneName': file.sceneName,
                          'monitored': str(movie.monitored),
                          'year': str(movie.year),
                          'sortTitle': movie.sortTitle,
                          'alternativeTitles': alternative_titles,
                          'format': video_format,
                          'resolution': resolution,
                          'video_codec': video_codec,
                          'audio_codec': audio_codec,
                          'overview': movie.overview,
                          'imdbId': movie.imdbId,
                          'movie_file_id': file.id,
                          'tags': str(tags),
                          'file_size': file.size,
                          'originalLanguage': original.name if original is not None else None}
    if profile_id is not KEEP_PROFILE:
        parsed['profileId'] = profile_id
    return parsed
