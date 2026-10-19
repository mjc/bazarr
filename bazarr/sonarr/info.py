# coding=utf-8

import logging
from hashlib import sha256
import semver
from dataclasses import dataclass

from utilities.arr import ArrVersion

from dogpile.cache import make_region
from dynaconf.utils.boxing import DynaBox

from app.config import settings, empty_values
from utilities.arr import discover_arr_version, parse_arr_version

region = make_region().configure('dogpile.cache.memory')


@dataclass(frozen=True)
class SonarrCapabilities:
    version: ArrVersion

    @property
    def legacy(self) -> bool:
        return self.version.numbers[0] in (0, 2)

    @property
    def separate_episode_files(self) -> bool:
        return not self.legacy and self.version.numbers < (4, 0, 9, 2421)

    @property
    def language_profiles(self) -> bool:
        return self.legacy or self.version.numbers[0] == 3


class GetSonarrInfo:
    @staticmethod
    def version() -> str:
        if not settings.general.use_sonarr:
            return ''
        configuration = settings.sonarr
        base_url = url_sonarr(configuration)
        cache_key = 'sonarr_version:' + sha256(
            f'{base_url}\0{configuration.apikey}'.encode()).hexdigest()
        cached_version = region.get(cache_key, expiration_time=60)
        if isinstance(cached_version, str) and cached_version and cached_version != 'unknown':
            return cached_version
        version = discover_arr_version(base_url, configuration.apikey, int(configuration.http_timeout))
        result = version if version is not None else 'unknown'
        region.set(cache_key, result)
        logging.debug('BAZARR got this Sonarr version from its API: %s', result)
        return result

    def semver(self) -> semver.Version | None:
        version = parse_arr_version(self.version())
        return None if version is None else version.semver()

    def capabilities(self) -> SonarrCapabilities | None:
        version = parse_arr_version(self.version())
        return None if version is None else SonarrCapabilities(version)

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


def url_sonarr(configuration: DynaBox | None = None) -> str:
    sonarr = settings.sonarr if configuration is None else configuration
    if sonarr.ssl:
        protocol_sonarr = "https"
    else:
        protocol_sonarr = "http"

    configured_base_url = sonarr.base_url
    base_url = configured_base_url
    if not base_url.startswith("/"):
        base_url = f"/{base_url}"
    if base_url.endswith("/"):
        base_url = base_url[:-1]
    if base_url != configured_base_url:
        sonarr.base_url = base_url

    configured_port = sonarr.port
    if configured_port in empty_values:
        port = ""
    else:
        port = f":{configured_port}"

    return f"{protocol_sonarr}://{sonarr.ip}{port}{base_url}"


def url_api_sonarr() -> str:
    return url_sonarr() + f'/api{"/v3" if not get_sonarr_info.is_legacy() else ""}/'
