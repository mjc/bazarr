# coding=utf-8

import requests
import logging
from typing import Annotated, TypeAlias

from pydantic import Field

from app.config import settings
from radarr.info import get_radarr_info, url_api_radarr
from constants import HEADERS
from utilities.arr_decoding import ArrRecordDecoder, DefaultOnError, InvalidAsNone, PartialResult, RecordPayload
from utilities.arr_types import ArrTag, AudioProfile, LegacyAudioProfile, RadarrAudioProfile
from radarr.movie_snapshot import IncompleteMovie, Movie

MovieRecord: TypeAlias = Annotated[
    Movie | IncompleteMovie, Field(union_mode='left_to_right'),
    DefaultOnError[Movie | IncompleteMovie](IncompleteMovie()),
]
MOVIE_DECODER = ArrRecordDecoder[Movie, IncompleteMovie](RecordPayload[Movie], RecordPayload[MovieRecord])

LEGACY_PROFILE_DECODER = ArrRecordDecoder[LegacyAudioProfile, None](
    RecordPayload[LegacyAudioProfile], RecordPayload[InvalidAsNone[LegacyAudioProfile]])
PROFILE_DECODER = ArrRecordDecoder[RadarrAudioProfile, None](
    RecordPayload[RadarrAudioProfile], RecordPayload[InvalidAsNone[RadarrAudioProfile]])
TAG_DECODER = ArrRecordDecoder[ArrTag, None](RecordPayload[ArrTag], RecordPayload[InvalidAsNone[ArrTag]])


def get_profile_list() -> list[AudioProfile] | None:
    apikey_radarr = settings.radarr.apikey
    # Get profiles data from radarr
    url_radarr_api_movies = (f"{url_api_radarr()}{'quality' if url_api_radarr().endswith('v3/') else ''}profile?"
                             f"apikey={apikey_radarr}")

    try:
        profiles_json = requests.get(url_radarr_api_movies, timeout=int(settings.radarr.http_timeout), verify=False,
                                     headers=HEADERS)
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get profiles from Radarr. Connection Error.")
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get profiles from Radarr. Timeout Error.")
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get profiles from Radarr.")
    else:
        with profiles_json:
            if profiles_json.status_code != 200:
                return None
            if get_radarr_info.is_legacy():
                profiles = LEGACY_PROFILE_DECODER.decode_response(profiles_json)
                return ([(profile.id, profile.language.capitalize()) for profile in profiles if profile is not None]
                        if profiles is not None else None)
            profiles = PROFILE_DECODER.decode_response(profiles_json)
            return ([(profile.id, profile.language.name.capitalize()) for profile in profiles if profile is not None]
                    if profiles is not None else None)
    return None


def get_tags() -> list[ArrTag] | None:
    apikey_radarr = settings.radarr.apikey

    # Get tags data from Radarr
    url_radarr_api_series = f"{url_api_radarr()}tag?apikey={apikey_radarr}"

    try:
        tagsDict = requests.get(url_radarr_api_series, timeout=int(settings.radarr.http_timeout), verify=False, headers=HEADERS)
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get tags from Radarr. Connection Error.")
        return None
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get tags from Radarr. Timeout Error.")
        return None
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get tags from Radarr.")
        return None
    else:
        with tagsDict:
            if tagsDict.status_code != 200:
                return None
            tags = TAG_DECODER.decode_response(tagsDict)
            return [tag for tag in tags if tag is not None] if tags is not None else None


def get_movies_from_radarr_api(apikey_radarr: str, radarr_id: int | None = None) -> PartialResult[Movie, IncompleteMovie]:
    url_radarr_api_movies = f'{url_api_radarr()}movie{f"/{radarr_id}" if radarr_id else ""}?apikey={apikey_radarr}'

    try:
        r = requests.get(url_radarr_api_movies, timeout=int(settings.radarr.http_timeout), verify=False, headers=HEADERS)
        if r.status_code == 404:
            return
        r.raise_for_status()
    except requests.exceptions.HTTPError:
        logging.exception("BAZARR Error trying to get movies from Radarr. Http error.")
        return
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get movies from Radarr. Connection Error.")
        return
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get movies from Radarr. Timeout Error.")
        return
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get movies from Radarr.")
        return
    except Exception as e:
        logging.exception(f"Exception raised while getting movies from Radarr API: {e}")
        return
    else:
        with r:
            if r.status_code != 200:
                return None
            return MOVIE_DECODER.decode_response(r)


def get_history_from_radarr_api(apikey_radarr, movie_id):
    url_radarr_api_history = f"{url_api_radarr()}history?eventType=1&movieIds={movie_id}&apikey={apikey_radarr}"

    try:
        r = requests.get(url_radarr_api_history, timeout=int(settings.radarr.http_timeout), verify=False,
                         headers=HEADERS)
        r.raise_for_status()
    except requests.exceptions.HTTPError:
        logging.exception("BAZARR Error trying to get history from Radarr. Http error.")
        return
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get history from Radarr. Connection Error.")
        return
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get history from Radarr. Timeout Error.")
        return
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get history from Radarr.")
        return
    except Exception as e:
        logging.exception(f"Exception raised while getting history from Radarr API: {e}")
        return
    else:
        if r.status_code == 200:
            return r.json()
        else:
            return
