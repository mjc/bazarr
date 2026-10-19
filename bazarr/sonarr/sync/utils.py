# coding=utf-8

import requests
import logging

from app.config import settings
from sonarr.info import SonarrConnection, get_sonarr_info, url_api_sonarr
from utilities.arr import request_json
from constants import HEADERS
from utilities.json_types import JsonValue


def get_profile_list():
    apikey_sonarr = settings.sonarr.apikey
    profiles_list = []

    # Get profiles data from Sonarr
    if get_sonarr_info.is_legacy():
        url_sonarr_api_series = f"{url_api_sonarr()}profile?apikey={apikey_sonarr}"
    else:
        if not get_sonarr_info.version().startswith('3.'):
            # return an empty list when using Sonarr >= v4 that does not support series languages profiles anymore
            return profiles_list
        url_sonarr_api_series = f"{url_api_sonarr()}languageprofile?apikey={apikey_sonarr}"

    try:
        profiles_json = requests.get(url_sonarr_api_series, timeout=int(settings.sonarr.http_timeout), verify=False,
                                     headers=HEADERS)
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get profiles from Sonarr. Connection Error.")
        return None
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get profiles from Sonarr. Timeout Error.")
        return None
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get profiles from Sonarr.")
        return None
    else:
        # Parsing data returned from Sonarr
        if get_sonarr_info.is_legacy():
            for profile in profiles_json.json():
                if not isinstance(profile, dict):
                    continue
                profile_id = profile.get('id')
                language = profile.get('language')
                if profile_id is not None and isinstance(language, str):
                    profiles_list.append([profile_id, language.capitalize()])
        else:
            for profile in profiles_json.json():
                if not isinstance(profile, dict):
                    continue
                profile_id = profile.get('id')
                name = profile.get('name')
                if profile_id is not None and isinstance(name, str):
                    profiles_list.append([profile_id, name.capitalize()])

    return profiles_list


def get_tags():
    apikey_sonarr = settings.sonarr.apikey
    tagsDict = []

    # Get tags data from Sonarr
    url_sonarr_api_series = f"{url_api_sonarr()}tag?apikey={apikey_sonarr}"

    try:
        tagsDict = requests.get(url_sonarr_api_series, timeout=int(settings.sonarr.http_timeout), verify=False, headers=HEADERS)
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get tags from Sonarr. Connection Error.")
        return []
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get tags from Sonarr. Timeout Error.")
        return []
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get tags from Sonarr.")
        return []
    else:
        return tagsDict.json()


def _request_sonarr(path: str, apikey: str, connection: SonarrConnection | None = None,
                     *, propagate_http_error: bool = False) -> JsonValue:
    api_url = url_api_sonarr() if connection is None else connection.api_url
    timeout = int(settings.sonarr.http_timeout) if connection is None else connection.timeout
    try:
        return request_json(f'{api_url}{path}&apikey={apikey}', timeout)
    except requests.exceptions.HTTPError:
        if propagate_http_error:
            raise
        logging.exception('BAZARR Error trying to get %s from Sonarr. Http error.', path.split('?')[0])
    except requests.exceptions.RequestException:
        logging.exception('BAZARR Error trying to get %s from Sonarr.', path.split('?')[0])
    return None


def get_series_from_sonarr_api(apikey_sonarr: str, sonarr_series_id: int | None = None,
                                *, connection: SonarrConnection | None = None) -> JsonValue:
    path = f"series/{sonarr_series_id if sonarr_series_id else ''}?"
    result = _request_sonarr(path, apikey_sonarr, connection, propagate_http_error=True)
    if sonarr_series_id and isinstance(result, dict):
        return [result]
    return result


def get_episodes_from_sonarr_api(apikey_sonarr: str, series_id: int | None = None,
                                episode_id: int | None = None,
                                *, connection: SonarrConnection | None = None) -> JsonValue:
    if series_id:
        path = f'episode?seriesId={series_id}&includeEpisodeFile=true'
    elif episode_id:
        path = f'episode/{episode_id}?'
    else:
        return None
    return _request_sonarr(path, apikey_sonarr, connection)


def get_episodesFiles_from_sonarr_api(apikey_sonarr: str, series_id: int | None = None,
                                     episode_file_id: int | None = None,
                                     *, connection: SonarrConnection | None = None) -> JsonValue:
    if series_id:
        path = f'episodeFile?seriesId={series_id}'
    elif episode_file_id:
        path = f'episodeFile/{episode_file_id}?'
    else:
        return None
    return _request_sonarr(path, apikey_sonarr, connection)


def get_history_from_sonarr_api(apikey_sonarr: str, episode_id: int) -> JsonValue:
    return _request_sonarr(f'history?eventType=1&episodeId={episode_id}', apikey_sonarr)
