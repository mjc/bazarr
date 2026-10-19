# coding=utf-8

import logging
from hashlib import sha256
import semver

from dogpile.cache import make_region
from dynaconf.utils.boxing import DynaBox

from app.config import settings, empty_values
from utilities.arr import discover_arr_version, parse_arr_version

region = make_region().configure('dogpile.cache.memory')


class GetRadarrInfo:
    @staticmethod
    def version() -> str:
        if not settings.general.use_radarr:
            return ''
        configuration = settings.radarr
        base_url = url_radarr(configuration)
        cache_key = 'radarr_version:' + sha256(
            f'{base_url}\0{configuration.apikey}'.encode()).hexdigest()
        cached_version = region.get(cache_key, expiration_time=60)
        if isinstance(cached_version, str) and cached_version and cached_version != 'unknown':
            return cached_version
        version = discover_arr_version(base_url, configuration.apikey, int(configuration.http_timeout))
        result = version if version is not None else 'unknown'
        region.set(cache_key, result)
        logging.debug('BAZARR got this Radarr version from its API: %s', result)
        return result

    def semver(self) -> semver.Version | None:
        version = parse_arr_version(self.version())
        return None if version is None else version.semver()

    def is_legacy(self) -> bool:
        """
        Call self.version() and parse the result to determine if it's a legacy version of Radarr
        @return: bool
        """
        radarr_version = self.version()
        if radarr_version.startswith('0.'):
            return True
        else:
            return False

    def is_deprecated(self) -> bool:
        """
                Call self.version() and parse the result to determine if it's a deprecated version of Radarr
                @return: bool
                """
        radarr_version = self.version()
        if radarr_version.startswith(('0.', '3.')):
            return True
        else:
            return False


get_radarr_info = GetRadarrInfo()


def url_radarr(configuration: DynaBox | None = None) -> str:
    radarr = settings.radarr if configuration is None else configuration
    if radarr.ssl:
        protocol_radarr = "https"
    else:
        protocol_radarr = "http"

    configured_base_url = radarr.base_url
    base_url = configured_base_url
    if not base_url.startswith("/"):
        base_url = f"/{base_url}"
    if base_url.endswith("/"):
        base_url = base_url[:-1]
    if base_url != configured_base_url:
        radarr.base_url = base_url

    configured_port = radarr.port
    if configured_port in empty_values:
        port = ""
    else:
        port = f":{configured_port}"

    return f"{protocol_radarr}://{radarr.ip}{port}{base_url}"


def url_api_radarr() -> str:
    return url_radarr() + f'/api{"/v3" if not get_radarr_info.is_legacy() else ""}/'
