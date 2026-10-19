# coding=utf-8

import requests
import logging

from urllib.parse import urlencode

from app.config import settings
from sonarr.info import url_api_sonarr
from constants import HEADERS
from utilities.arr import arr_session
from utilities.json_types import JsonValue


def browse_sonarr_filesystem(path: str = '#', *, session: requests.Session | None = None) -> JsonValue:
    if path == '#':
        path = ''
    query = urlencode({
        'path': path,
        'allowFoldersWithoutTrailingSlashes': 'true',
        'includeFiles': 'false',
        'apikey': settings.sonarr.apikey,
    })
    with arr_session(session) as http:
        url_sonarr_api_filesystem = f"{url_api_sonarr(session=http)}filesystem?{query}"
        try:
            with http.get(url_sonarr_api_filesystem, timeout=int(settings.sonarr.http_timeout), verify=False,
                             headers=HEADERS) as r:
                r.raise_for_status()
        except requests.exceptions.HTTPError:
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

        payload: JsonValue = r.json()
        return payload
