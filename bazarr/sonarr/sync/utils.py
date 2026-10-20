# coding=utf-8

import requests
import logging

from app.config import settings
from sonarr.info import SonarrConnection, get_sonarr_info, url_api_sonarr
from utilities.arr import arr_session, request_json
from utilities.json_types import JsonValue
from utilities.arr_decoding import ArrRecordDecoder, InvalidArrRecord
from collections.abc import Callable
from typing import TypeVar, overload

from sonarr.series_snapshot import Series
from sonarr.episode_snapshot import Episode
from sonarr.episode_snapshot import EpisodeFile

SERIES_DECODER = ArrRecordDecoder(Series)
EPISODE_DECODER = ArrRecordDecoder(Episode)
EPISODEFILE_DECODER = ArrRecordDecoder(EpisodeFile)

Result = TypeVar('Result')


def get_profile_list(*, connection: SonarrConnection | None = None,
                        session: requests.Session | None = None) -> list[list[int | str]] | None:
    with arr_session(session) as http:
        apikey = settings.sonarr.apikey if connection is None else connection.apikey
        legacy = get_sonarr_info.is_legacy(session=http) if connection is None else connection.capabilities.legacy
        supports_profiles = (legacy or get_sonarr_info.version(session=http).startswith('3.')) if connection is None else connection.capabilities.language_profiles
        if not legacy and not supports_profiles:
            return []
        path = 'profile?' if legacy else 'languageprofile?'
        profiles = _request_sonarr(path, apikey, connection, session=http)
        if not isinstance(profiles, list):
            return None
        result: list[list[int | str]] = []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_id = profile.get('id')
            name = profile.get('language' if legacy else 'name')
            if type(profile_id) is int and isinstance(name, str):
                result.append([profile_id, name.capitalize()])
        return result


def get_tags(*, connection: SonarrConnection | None = None,
                session: requests.Session | None = None) -> JsonValue:
    apikey = settings.sonarr.apikey if connection is None else connection.apikey
    return _request_sonarr('tag?', apikey, connection, session=session)


@overload
def _request_sonarr(path: str, apikey: str, connection: SonarrConnection | None = None,
                     *, propagate_http_error: bool = False, session: requests.Session | None = None) -> JsonValue: ...


@overload
def _request_sonarr(path: str, apikey: str, connection: SonarrConnection | None = None,
                     *, propagate_http_error: bool = False, session: requests.Session | None = None,
                     decoder: Callable[[requests.Response], Result]) -> Result | None: ...


def _request_sonarr(path: str, apikey: str, connection: SonarrConnection | None = None,
                     *, propagate_http_error: bool = False, session: requests.Session | None = None,
                     decoder: Callable[[requests.Response], Result] | None = None) -> JsonValue | Result:
    with arr_session(session) as http:
        api_url = url_api_sonarr(session=http) if connection is None else connection.api_url
        timeout = int(settings.sonarr.http_timeout) if connection is None else connection.timeout
        try:
            url = f'{api_url}{path}&apikey={apikey}'
            if decoder is None:
                return request_json(url, timeout, session=http)
            return request_json(url, timeout, session=http, decoder=decoder)
        except requests.exceptions.HTTPError:
            if propagate_http_error:
                raise
            logging.exception('BAZARR Error trying to get %s from Sonarr. Http error.', path.split('?')[0])
        except requests.exceptions.RequestException:
            logging.exception('BAZARR Error trying to get %s from Sonarr.', path.split('?')[0])
        return None


def get_series_from_sonarr_api(apikey_sonarr: str, sonarr_series_id: int | None = None,
                                *, connection: SonarrConnection | None = None,
                                   session: requests.Session | None = None) -> Series | list[Series | InvalidArrRecord] | InvalidArrRecord | None:
    path = f"series/{sonarr_series_id if sonarr_series_id else ''}?"
    result = _request_sonarr(path, apikey_sonarr, connection, propagate_http_error=True, session=session, decoder=SERIES_DECODER.decode_response)
    if sonarr_series_id and isinstance(result, (Series, InvalidArrRecord)):
        return [result]
    return result


def get_episodes_from_sonarr_api(apikey_sonarr: str, series_id: int | None = None,
                                episode_id: int | None = None,
                                *, connection: SonarrConnection | None = None,
                                   session: requests.Session | None = None) -> Episode | list[Episode] | None:
    if series_id:
        path = f'episode?seriesId={series_id}&includeEpisodeFile=true'
    elif episode_id:
        path = f'episode/{episode_id}?'
    else:
        return None
    return _request_sonarr(path, apikey_sonarr, connection, session=session, decoder=EPISODE_DECODER.decode_complete_response)


def get_episodesFiles_from_sonarr_api(apikey_sonarr: str, series_id: int | None = None,
                                     episode_file_id: int | None = None,
                                     *, connection: SonarrConnection | None = None,
                                        session: requests.Session | None = None) -> EpisodeFile | list[EpisodeFile] | None:
    if series_id:
        path = f'episodeFile?seriesId={series_id}'
    elif episode_file_id:
        path = f'episodeFile/{episode_file_id}?'
    else:
        return None
    return _request_sonarr(path, apikey_sonarr, connection, session=session, decoder=EPISODEFILE_DECODER.decode_complete_response)


def get_history_from_sonarr_api(apikey_sonarr: str, episode_id: int,
                                  *, session: requests.Session | None = None) -> JsonValue:
    return _request_sonarr(f'history?eventType=1&episodeId={episode_id}', apikey_sonarr, session=session)
