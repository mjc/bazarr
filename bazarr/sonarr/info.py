# coding=utf-8

import logging
import requests
import datetime
import semver

from requests.exceptions import JSONDecodeError, RequestException

from dogpile.cache import make_region

from app.config import settings, empty_values
from constants import HEADERS

region = make_region().configure('dogpile.cache.memory')


class InvalidSonarrStatus(ValueError):
    """The status response does not contain a usable version string."""


def _parse_version(payload: object) -> str:
    if isinstance(payload, dict):
        version: object = payload.get('version')
        if isinstance(version, str) and version:
            return version
    raise InvalidSonarrStatus('Sonarr status has no nonempty version string')


class GetSonarrInfo:
    @staticmethod
    def version() -> str:
        """
        Call system/status API endpoint and get the Sonarr version
        @return: str
        """
        cached_version: object = region.get("sonarr_version",
                                            expiration_time=datetime.timedelta(seconds=60).total_seconds())
        if isinstance(cached_version, str) and cached_version and cached_version != 'unknown':
            region.set("sonarr_version", cached_version)
            return cached_version
        sonarr_version = ''
        if settings.general.use_sonarr:
            try:
                sv = f"{url_sonarr()}/api/system/status?apikey={settings.sonarr.apikey}"
                sonarr_json: object = requests.get(sv, timeout=int(settings.sonarr.http_timeout), verify=False,
                                                   headers=HEADERS).json()
                sonarr_version = _parse_version(sonarr_json)
            except (JSONDecodeError, InvalidSonarrStatus):
                try:
                    sv = f"{url_sonarr()}/api/v3/system/status?apikey={settings.sonarr.apikey}"
                    sonarr_json = requests.get(sv, timeout=int(settings.sonarr.http_timeout), verify=False,
                                               headers=HEADERS).json()
                    sonarr_version = _parse_version(sonarr_json)
                except (RequestException, InvalidSonarrStatus):
                    logging.debug('BAZARR cannot get Sonarr version')
                    sonarr_version = 'unknown'
            except Exception:
                logging.debug('BAZARR cannot get Sonarr version')
                sonarr_version = 'unknown'
        logging.debug(f'BAZARR got this Sonarr version from its API: {sonarr_version}')
        region.set("sonarr_version", sonarr_version)
        return sonarr_version

    def semver(self) -> semver.Version | None:
        semver_version = None
        if isinstance(self.version(), str) and self.version() not in ['', 'unknown']:
            split_version = self.version().split('.')
            if len(split_version) >= 3 and all(
                    split_version[i].isdigit() for i in range(len(split_version))):
                semver_version = semver.Version(int(split_version[0]), int(split_version[1]), int(split_version[2]),
                                                *split_version[3:])
        return semver_version

    def is_legacy(self) -> bool:
        """
        Call self.version() and parse the result to determine if it's a legacy version of Sonarr API
        @return: bool
        """
        sonarr_version = self.version()
        if sonarr_version.startswith(('0.', '2.')):
            return True
        else:
            return False

    def is_deprecated(self) -> bool:
        """
                Call self.version() and parse the result to determine if it's a deprecated version of Sonarr
                @return: bool
                """
        sonarr_version = self.version()
        if sonarr_version.startswith(('0.', '2.')):
            return True
        else:
            return False


get_sonarr_info = GetSonarrInfo()


def url_sonarr() -> str:
    if settings.sonarr.ssl:
        protocol_sonarr = "https"
    else:
        protocol_sonarr = "http"

    if settings.sonarr.base_url == '':
        settings.sonarr.base_url = "/"
    if not settings.sonarr.base_url.startswith("/"):
        settings.sonarr.base_url = f"/{settings.sonarr.base_url}"
    if settings.sonarr.base_url.endswith("/"):
        settings.sonarr.base_url = settings.sonarr.base_url[:-1]

    if settings.sonarr.port in empty_values:
        port = ""
    else:
        port = f":{settings.sonarr.port}"

    return f"{protocol_sonarr}://{settings.sonarr.ip}{port}{settings.sonarr.base_url}"


def url_api_sonarr() -> str:
    return url_sonarr() + f'/api{"/v3" if not get_sonarr_info.is_legacy() else ""}/'
