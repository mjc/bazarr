# coding=utf-8

import ast

from functools import wraps
from flask import request, abort
from operator import itemgetter

from app.config import settings, base_url
from languages.get_languages import language_from_alpha2, alpha3_from_alpha2
from app.database import get_audio_profile_languages, get_desired_languages, get_subtitles
from utilities.path_mappings import path_mappings

None_Keys = ['null', 'undefined', '', None]

False_Keys = ['False', 'false', '0']


def _safe_literal_list(value):
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_missing_subtitles(missing_subtitles):
    parsed_missing = []
    for subs in _safe_literal_list(missing_subtitles):
        if not isinstance(subs, str):
            continue
        language = subs.split(':')
        parsed_missing.append(
            {
                "name": language_from_alpha2(language[0]),
                "code2": language[0],
                "code3": alpha3_from_alpha2(language[0]),
                "forced": len(language) > 1 and language[1] == 'forced',
                "hi": len(language) > 1 and language[1] == 'hi',
            }
        )
    return parsed_missing


def _parse_language_value(language):
    if language == 'None':
        return None
    if not isinstance(language, str):
        return None

    base_language = language.split(':', 1)[0].strip()
    if not base_language:
        return None

    return {
        "name": language_from_alpha2(base_language),
        "code2": base_language,
        "code3": alpha3_from_alpha2(base_language),
        "forced": language.endswith(':forced'),
        "hi": language.endswith(':hi'),
    }


def authenticate(actual_method):
    @wraps(actual_method)
    def wrapper(*args, **kwargs):
        apikey_settings = settings.auth.apikey
        apikey_get = request.args.get('apikey')
        apikey_post = request.form.get('apikey')
        apikey_header = None
        if 'X-API-KEY' in request.headers:
            apikey_header = request.headers['X-API-KEY']

        if apikey_settings in [apikey_get, apikey_post, apikey_header]:
            return actual_method(*args, **kwargs)

        return abort(401)

    return wrapper


def postprocess(item):
    # Remove ffprobe_cache
    if item.get('radarrId'):
        path_replace = path_mappings.path_replace_movie
    else:
        path_replace = path_mappings.path_replace
    if item.get('ffprobe_cache'):
        del item['ffprobe_cache']

    # Parse audio language
    if item.get('audio_language'):
        item['audio_language'] = get_audio_profile_languages(item['audio_language'])

    # Make sure profileId is a valid None value
    if item.get('profileId') in None_Keys:
        item['profileId'] = None

    # Parse alternate titles
    if item.get('alternativeTitles'):
        item['alternativeTitles'] = _safe_literal_list(item['alternativeTitles'])
    else:
        item['alternativeTitles'] = []

    # Add subtitles
    item['subtitles'] = get_subtitles(sonarr_episode_id=item.get('sonarrEpisodeId'),
                                      radarr_id=item.get('radarrId'))

    if settings.general.embedded_subs_show_desired and item.get('profileId'):
        desired_lang_list = get_desired_languages(item['profileId'])
        item['subtitles'] = [x for x in item['subtitles'] if x['code2'] in desired_lang_list or x['path']]
        item['subtitles'] = sorted(item['subtitles'], key=itemgetter('name', 'forced'))

    # Parse missing subtitles
    if item.get('missing_subtitles'):
        item['missing_subtitles'] = _parse_missing_subtitles(item['missing_subtitles'])
    else:
        item['missing_subtitles'] = []

    # Parse tags
    if item.get('tags') is not None:
        item['tags'] = _safe_literal_list(item.get('tags', '[]'))
    else:
        item['tags'] = []
    if item.get('monitored'):
        item['monitored'] = item.get('monitored') == 'True'
    else:
        item['monitored'] = False
    if item.get('hearing_impaired'):
        item['hearing_impaired'] = item.get('hearing_impaired') == 'True'
    else:
        item['hearing_impaired'] = False

    if item.get('language') is not None:
        item['language'] = _parse_language_value(item.get('language'))

    if item.get('path'):
        item['path'] = path_replace(item['path'])

    if item.get('video_path'):
        # Provide mapped video path for history
        item['video_path'] = path_replace(item['video_path'])

    if item.get('subtitles_path'):
        # Provide mapped subtitles path
        item['subtitles_path'] = path_replace(item['subtitles_path'])

    if item.get('external_subtitles'):
        # Provide mapped external subtitles paths for history
        item['external_subtitles'] = path_replace(item['external_subtitles'])

    # map poster and fanart to server proxy
    if item.get('poster') is not None:
        poster = item['poster']
        item['poster'] = f"{base_url}/images/{'movies' if item.get('radarrId') else 'series'}{poster}" if poster else None

    if item.get('fanart') is not None:
        fanart = item['fanart']
        item['fanart'] = f"{base_url}/images/{'movies' if item.get('radarrId') else 'series'}{fanart}" if fanart else None

    return item
