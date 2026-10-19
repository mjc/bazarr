# coding=utf-8

import os

from dynaconf.utils.boxing import DynaBox

from app.config import settings
from languages.get_languages import audio_language_from_name
from radarr.info import RadarrCapabilities, get_radarr_info
from radarr.movie_snapshot import Movie, MovieValues
from utilities.arr_types import ArrTag
from utilities.video_analyzer import embedded_audio_reader
from utilities.path_mappings import path_mappings

from .converter import RadarrFormatAudioCodec, RadarrFormatVideoCodec


def get_matching_profile(tags, language_profiles):
    matching_profile = None
    if len(tags) > 0:
        for profileId, name, tag in language_profiles:
            if tag in tags:
                matching_profile = profileId
                break
    return matching_profile


def movieParser(movie: Movie, action: str, tags_dict: list[ArrTag], language_profiles,
                movie_default_profile: int | None, audio_profiles,
                *, general: DynaBox | None = None,
                capabilities: RadarrCapabilities | None = None,
                parse_embedded_audio_track: bool | None = None) -> MovieValues | None:
    file = movie['movieFile']
    legacy = get_radarr_info.is_legacy() if capabilities is None else capabilities.legacy
    images = movie.get('images') or []
    poster = ''
    if images:
        poster_big = images[0]['url']
        poster = f'{os.path.splitext(poster_big)[0]}-500{os.path.splitext(poster_big)[1]}'
    fanart = images[1]['url'] if len(images) > 1 else ''
    titles = movie.get('alternativeTitles') if legacy else movie.get('alternateTitles')
    alternative_titles = str([item['title'] for item in titles]) if titles is not None else None
    quality = file['quality']['quality']
    try:
        video_format, resolution = quality['name'].split('-')
    except ValueError:
        video_format = quality['name']
        quality_resolution = quality.get('resolution')
        resolution = f'{quality_resolution}p' if quality_resolution is not None else None
    media = file.get('mediaInfo')
    if media:
        video_codec = RadarrFormatVideoCodec(media.get('videoFormat' if legacy else 'videoCodec'),
                                             media.get('videoCodecID'), media.get('videoCodecLibrary'))
        audio_codec = RadarrFormatAudioCodec(media.get('audioFormat' if legacy else 'audioCodec'),
                                             media.get('audioCodecID'), media.get('audioProfile'),
                                             media.get('audioAdditionalFeatures'))
    else:
        video_codec = audio_codec = None
    general_settings = settings.general if general is None else general
    parse_audio = general_settings.parse_embedded_audio_track if parse_embedded_audio_track is None else parse_embedded_audio_track
    if parse_audio:
        audio_language = embedded_audio_reader(path_mappings.path_replace_movie(file['path']),
                                               file_size=int(file['size']), movie_file_id=file['id'], use_cache=True)
    elif legacy:
        audio_languages = media.get('audioLanguages') if media else None
        audio_language = [language.strip() for language in audio_languages.split('/')] if audio_languages is not None else []
        if not audio_language:
            audio_language = profile_id_to_language(movie.get('qualityProfileId'), audio_profiles)
    else:
        audio_language = [audio_language_from_name(language['name']) for language in file.get('languages') or []]
    tags = [tag['label'] for tag in tags_dict if tag['id'] in movie['tags']]
    original = movie.get('originalLanguage')
    parsed: MovieValues = {'radarrId': movie['id'],
                          'title': movie['title'],
                          'path': file['path'],
                          'tmdbId': str(movie['tmdbId']),
                          'poster': poster,
                          'fanart': fanart,
                          'audio_language': str(audio_language),
                          'sceneName': file.get('sceneName'),
                          'monitored': str(movie['monitored']),
                          'year': str(movie['year']),
                          'sortTitle': movie['sortTitle'],
                          'alternativeTitles': alternative_titles,
                          'format': video_format,
                          'resolution': resolution,
                          'video_codec': video_codec,
                          'audio_codec': audio_codec,
                          'overview': str(movie.get('overview', '')),
                          'imdbId': movie.get('imdbId'),
                          'movie_file_id': file['id'],
                          'tags': str(tags),
                          'file_size': int(file['size']),
                          'originalLanguage': original.get('name') if original is not None else None}
    if action == 'insert':
        parsed['profileId'] = movie_default_profile
    if general_settings.movie_tag_enabled:
        tag_profile = get_matching_profile(tags, language_profiles)
        if tag_profile:
            parsed['profileId'] = tag_profile
        if set(tags) & set(general_settings.remove_profile_tags):
            parsed['profileId'] = None
    return parsed


def profile_id_to_language(id, profiles):
    profiles_to_return = []
    for profile in profiles:
        if id == profile[0]:
            profiles_to_return.append(profile[1])
    return profiles_to_return
