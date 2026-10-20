# coding=utf-8

import requests
import logging

from urllib.parse import urlencode

from app.config import settings
from radarr.info import url_api_radarr
from constants import HEADERS
from utilities.arr import arr_session, decode_arr_response
from utilities.json_types import JsonValue


def browse_radarr_filesystem(path: str = '#', *, session: requests.Session | None = None) -> JsonValue:
    if path == '#':
        path = ''

    query = urlencode({
        'path': path,
        'allowFoldersWithoutTrailingSlashes': 'true',
        'includeFiles': 'false',
        'apikey': settings.radarr.apikey,
    })
    with arr_session(session) as http:
        url_radarr_api_filesystem = f"{url_api_radarr(session=http)}filesystem?{query}"
        try:
            with http.get(url_radarr_api_filesystem, timeout=int(settings.radarr.http_timeout), verify=False,
                             headers=HEADERS, stream=True) as r:
                r.raise_for_status()
                payload = decode_arr_response(r)
        except requests.exceptions.JSONDecodeError:
            raise
        except requests.exceptions.HTTPError:
            logging.exception("BAZARR Error trying to get series from Radarr. Http error.")
            return
        except requests.exceptions.ConnectionError:
            logging.exception("BAZARR Error trying to get series from Radarr. Connection Error.")
            return
        except requests.exceptions.Timeout:
            logging.exception("BAZARR Error trying to get series from Radarr. Timeout Error.")
            return
        except requests.exceptions.RequestException:
            logging.exception("BAZARR Error trying to get series from Radarr.")
            return

        return payload
