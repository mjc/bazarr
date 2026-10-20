# coding=utf-8

import requests
import logging
import semver

from app.config import settings
from sonarr.info import get_sonarr_info, url_api_sonarr
from constants import HEADERS
from utilities.arr_decoding import ArrRecordDecoder, CompleteResult, InvalidAsNone, RecordPayload
from utilities.arr_types import ArrTag, AudioProfile, LegacyAudioProfile, SonarrAudioProfile
from sonarr.series_snapshot import Series
from sonarr.episode_snapshot import Episode, EpisodeFile, complete_episode_snapshot

SERIES_DECODER = ArrRecordDecoder[Series, None](RecordPayload[Series], RecordPayload[InvalidAsNone[Series]])
EPISODE_DECODER = ArrRecordDecoder[Episode, None](RecordPayload[Episode], RecordPayload[InvalidAsNone[Episode]])
EPISODEFILE_DECODER = ArrRecordDecoder[EpisodeFile, None](RecordPayload[EpisodeFile], RecordPayload[InvalidAsNone[EpisodeFile]])

LEGACY_PROFILE_DECODER = ArrRecordDecoder[LegacyAudioProfile, None](
    RecordPayload[LegacyAudioProfile], RecordPayload[InvalidAsNone[LegacyAudioProfile]])
PROFILE_DECODER = ArrRecordDecoder[SonarrAudioProfile, None](
    RecordPayload[SonarrAudioProfile], RecordPayload[InvalidAsNone[SonarrAudioProfile]])
TAG_DECODER = ArrRecordDecoder[ArrTag, None](RecordPayload[ArrTag], RecordPayload[InvalidAsNone[ArrTag]])


def get_profile_list() -> list[AudioProfile] | None:
    apikey_sonarr = settings.sonarr.apikey

    # Get profiles data from Sonarr
    legacy = get_sonarr_info.is_legacy()
    if legacy:
        url_sonarr_api_series = f"{url_api_sonarr()}profile?apikey={apikey_sonarr}"
    else:
        if not get_sonarr_info.version().startswith('3.'):
            # return an empty list when using Sonarr >= v4 that does not support series languages profiles anymore
            return []
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
        with profiles_json:
            if profiles_json.status_code != 200:
                return None
            if legacy:
                profiles = LEGACY_PROFILE_DECODER.decode_response(profiles_json)
                return ([(profile.id, profile.language.capitalize()) for profile in profiles if profile is not None]
                        if profiles is not None else None)
            profiles = PROFILE_DECODER.decode_response(profiles_json)
            return ([(profile.id, profile.name.capitalize()) for profile in profiles if profile is not None]
                    if profiles is not None else None)


def get_tags() -> list[ArrTag] | None:
    apikey_sonarr = settings.sonarr.apikey

    # Get tags data from Sonarr
    url_sonarr_api_series = f"{url_api_sonarr()}tag?apikey={apikey_sonarr}"

    try:
        tagsDict = requests.get(url_sonarr_api_series, timeout=int(settings.sonarr.http_timeout), verify=False, headers=HEADERS)
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get tags from Sonarr. Connection Error.")
        return None
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get tags from Sonarr. Timeout Error.")
        return None
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get tags from Sonarr.")
        return None
    else:
        with tagsDict:
            if tagsDict.status_code != 200:
                return None
            tags = TAG_DECODER.decode_response(tagsDict)
            return [tag for tag in tags if tag is not None] if tags is not None else None


def get_series_from_sonarr_api(apikey_sonarr: str, sonarr_series_id: int | None = None) -> list[Series | None] | None:
    url_sonarr_api_series = (f"{url_api_sonarr()}series/{sonarr_series_id if sonarr_series_id else ''}?"
                             f"apikey={apikey_sonarr}")
    try:
        r = requests.get(url_sonarr_api_series, timeout=int(settings.sonarr.http_timeout), verify=False, headers=HEADERS)
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code:
            raise requests.exceptions.HTTPError
        logging.exception("BAZARR Error trying to get series from Sonarr. Http error.")
        return
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get series from Sonarr. Connection Error.")
        return
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get series from Sonarr. Timeout Error.")
        return
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get series from Sonarr.")
        return
    except Exception as e:
        logging.exception(f"Exception raised while getting series from Sonarr API: {e}")
        return
    else:
        with r:
            if r.status_code != 200:
                return None
            result = SERIES_DECODER.decode_response(r, single=sonarr_series_id is not None)
            if sonarr_series_id is not None and result and (result[0] is None or result[0].id != sonarr_series_id):
                return None
            return result


def get_episodes_from_sonarr_api(apikey_sonarr: str, series_id: int | None = None, episode_id: int | None = None) -> CompleteResult[Episode]:
    if series_id:
        url_sonarr_api_episode = (f"{url_api_sonarr()}episode?seriesId={series_id}&includeEpisodeFile=true&"
                                  f"apikey={apikey_sonarr}")
    elif episode_id:
        url_sonarr_api_episode = f"{url_api_sonarr()}episode/{episode_id}?apikey={apikey_sonarr}"
    else:
        return

    try:
        r = requests.get(url_sonarr_api_episode, timeout=int(settings.sonarr.http_timeout), verify=False, headers=HEADERS)
        r.raise_for_status()
    except requests.exceptions.HTTPError:
        logging.exception("BAZARR Error trying to get episodes from Sonarr. Http error.")
        return
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get episodes from Sonarr. Connection Error.")
        return
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get episodes from Sonarr. Timeout Error.")
        return
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get episodes from Sonarr.")
        return
    except Exception as e:
        logging.exception(f"Exception raised while getting episodes from Sonarr API: {e}")
        return
    else:
        with r:
            if r.status_code != 200:
                return None
            episodes = EPISODE_DECODER.decode_complete_response(r, single=series_id is None)
        del r
        if episodes is not None and any(episode.seriesId != series_id if series_id is not None
                                        else episode.id != episode_id for episode in episodes):
            logging.warning('BAZARR Skipping episode sync for series %s: response contains another series '
                            'or episode.', series_id)
            return None
        if not episodes:
            return episodes
        source_series_id = episodes[0].seriesId
        separate = False
        if not get_sonarr_info.is_legacy():
            version = get_sonarr_info.semver()
            if version is None:
                logging.warning('BAZARR Skipping episode sync for series %s: Sonarr version is unavailable.', source_series_id)
                return None
            separate = version < semver.Version(4, 0, 9, '2421')
        files: list[EpisodeFile] = []
        if separate:
            if series_id is not None:
                result = get_episodesFiles_from_sonarr_api(apikey_sonarr=apikey_sonarr, series_id=series_id)
                if result is None:
                    logging.warning('BAZARR Skipping episode sync for series %s: invalid episode-file response.', series_id)
                    return None
                files = result
            elif episodes[0].hasFile:
                file_id = episodes[0].episodeFileId
                if not file_id:
                    logging.warning('BAZARR Skipping episode %s: episodeFileId is missing.', episodes[0].id)
                    return None
                result = get_episodesFiles_from_sonarr_api(apikey_sonarr=apikey_sonarr, episode_file_id=file_id)
                if result is None:
                    logging.warning('BAZARR Skipping episode %s: invalid response for file %s.', episodes[0].id, file_id)
                    return None
                files = result
        completed = complete_episode_snapshot(episodes, files, separate_files=separate)
        if completed is None:
            logging.warning('BAZARR Skipping episode sync for series %s: incomplete or duplicate episode-file metadata.',
                            source_series_id)
        return completed


def get_episodesFiles_from_sonarr_api(apikey_sonarr: str, series_id: int | None = None, episode_file_id: int | None = None) -> CompleteResult[EpisodeFile]:
    if series_id:
        url_sonarr_api_episodeFiles = f"{url_api_sonarr()}episodeFile?seriesId={series_id}&apikey={apikey_sonarr}"
    elif episode_file_id:
        url_sonarr_api_episodeFiles = f"{url_api_sonarr()}episodeFile/{episode_file_id}?apikey={apikey_sonarr}"
    else:
        return

    try:
        r = requests.get(url_sonarr_api_episodeFiles, timeout=int(settings.sonarr.http_timeout), verify=False,
                         headers=HEADERS)
        r.raise_for_status()
    except requests.exceptions.HTTPError:
        logging.exception("BAZARR Error trying to get episodeFiles from Sonarr. Http error.")
        return
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get episodeFiles from Sonarr. Connection Error.")
        return
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get episodeFiles from Sonarr. Timeout Error.")
        return
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get episodeFiles from Sonarr.")
        return
    except Exception as e:
        logging.exception(f"Exception raised while getting episodes from Sonarr API: {e}")
        return
    else:
        with r:
            if r.status_code != 200:
                return None
            files = EPISODEFILE_DECODER.decode_complete_response(r, single=episode_file_id is not None)
            if episode_file_id is not None and files and files[0].id != episode_file_id:
                return None
            return files


def get_history_from_sonarr_api(apikey_sonarr, episode_id):
    url_sonarr_api_history = f"{url_api_sonarr()}history?eventType=1&episodeId={episode_id}&apikey={apikey_sonarr}"

    try:
        r = requests.get(url_sonarr_api_history, timeout=int(settings.sonarr.http_timeout), verify=False,
                         headers=HEADERS)
        r.raise_for_status()
    except requests.exceptions.HTTPError:
        logging.exception("BAZARR Error trying to get history from Sonarr. Http error.")
        return
    except requests.exceptions.ConnectionError:
        logging.exception("BAZARR Error trying to get history from Sonarr. Connection Error.")
        return
    except requests.exceptions.Timeout:
        logging.exception("BAZARR Error trying to get history from Sonarr. Timeout Error.")
        return
    except requests.exceptions.RequestException:
        logging.exception("BAZARR Error trying to get history from Sonarr.")
        return
    except Exception as e:
        logging.exception(f"Exception raised while getting history from Sonarr API: {e}")
        return
    else:
        if r.status_code == 200:
            return r.json()
        else:
            return
