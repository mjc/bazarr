# coding=utf-8

import logging
import requests

from app.config import settings
from radarr.info import url_api_radarr
from constants import HEADERS
from utilities.arr import arr_session


def notify_radarr(radarr_id: int | str, *, session: requests.Session | None = None) -> None:
    try:
        with arr_session(session) as http:
            url = f"{url_api_radarr(session=http)}command?apikey={settings.radarr.apikey}"
            data = {
                'name': 'RescanMovie',
                'movieId': int(radarr_id)
            }
            with http.post(url, json=data, timeout=int(settings.radarr.http_timeout), verify=False, headers=HEADERS):
                pass
    except Exception:
        logging.exception('BAZARR cannot notify Radarr')
