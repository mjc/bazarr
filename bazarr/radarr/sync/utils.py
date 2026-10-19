# coding=utf-8

import requests
import logging

from app.config import settings
from radarr.info import RadarrConnection, get_radarr_info, url_api_radarr
from utilities.arr import request_json
from utilities.json_types import JsonValue


def _request_radarr(path: str, apikey: str, connection: RadarrConnection | None = None) -> JsonValue:
    api_url = url_api_radarr() if connection is None else connection.api_url
    timeout = int(settings.radarr.http_timeout) if connection is None else connection.timeout
    try:
        return request_json(f'{api_url}{path}&apikey={apikey}', timeout)
    except requests.exceptions.RequestException:
        logging.exception('BAZARR Error trying to get %s from Radarr.', path.split('?')[0])
        return None


def get_profile_list(*, connection: RadarrConnection | None = None) -> list[list[int | str]] | None:
    apikey = settings.radarr.apikey if connection is None else connection.apikey
    legacy = get_radarr_info.is_legacy() if connection is None else connection.capabilities.legacy
    api_url = url_api_radarr() if connection is None else connection.api_url
    path = 'qualityprofile?' if api_url.endswith('v3/') else 'profile?'
    profiles = _request_radarr(path, apikey, connection)
    if not isinstance(profiles, list):
        return None
    result: list[list[int | str]] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = profile.get('id')
        language = profile.get('language')
        name = language if legacy else language.get('name') if isinstance(language, dict) else None
        if type(profile_id) is int and isinstance(name, str):
            result.append([profile_id, name.capitalize()])
    return result


def get_tags(*, connection: RadarrConnection | None = None) -> JsonValue:
    apikey = settings.radarr.apikey if connection is None else connection.apikey
    return _request_radarr('tag?', apikey, connection)


def get_movies_from_radarr_api(apikey_radarr: str, radarr_id: int | None = None,
                               *, connection: RadarrConnection | None = None) -> JsonValue:
    return _request_radarr(f'movie{f"/{radarr_id}" if radarr_id else ""}?', apikey_radarr, connection)


def get_history_from_radarr_api(apikey_radarr: str, movie_id: int) -> JsonValue:
    return _request_radarr(f'history?eventType=1&movieIds={movie_id}', apikey_radarr)
