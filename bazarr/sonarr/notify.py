# coding=utf-8

import logging
import requests

from app.config import settings
from sonarr.info import url_api_sonarr
from constants import HEADERS
from utilities.arr import arr_session


def notify_sonarr(sonarr_series_id: int | str, *, session: requests.Session | None = None) -> None:
    try:
        with arr_session(session) as http:
            url = f"{url_api_sonarr(session=http)}command?apikey={settings.sonarr.apikey}"
            data = {
                'name': 'RescanSeries',
                'seriesId': int(sonarr_series_id)
            }
            with http.post(url, json=data, timeout=int(settings.sonarr.http_timeout), verify=False, headers=HEADERS):
                pass
    except Exception:
        logging.exception('BAZARR cannot notify Sonarr')
