# coding=utf-8

from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import contextmanager

import requests
import semver

from constants import HEADERS
from utilities.json_types import JsonValue


@dataclass(frozen=True)
class ArrVersion:
    text: str
    numbers: tuple[int, int, int, int]

    def semver(self) -> semver.Version:
        major, minor, patch, revision = self.numbers
        return semver.Version(major, minor, patch, str(revision) if len(self.text.split('.')) == 4 else None)


def parse_arr_version(version: str) -> ArrVersion | None:
    parts = version.split('.')
    if len(parts) not in (3, 4) or not all(part.isascii() and part.isdigit() for part in parts):
        return None
    major, minor, patch = (int(part) for part in parts[:3])
    return ArrVersion(version, (major, minor, patch, int(parts[3]) if len(parts) == 4 else 0))


@contextmanager
def arr_session(session: requests.Session | None = None) -> Iterator[requests.Session]:
    # The former per-request sessions did not carry cookies into later requests.
    if session is not None:
        try:
            yield session
        finally:
            session.cookies.clear()
        return
    with requests.Session() as owned:
        try:
            yield owned
        finally:
            owned.cookies.clear()


def request_json(url: str, timeout: int, *, session: requests.Session | None = None) -> JsonValue:
    with arr_session(session) as http:
        with http.get(url, timeout=timeout, verify=False, headers=HEADERS) as response:
            response.raise_for_status()
            payload: JsonValue = response.json()
            return payload


def discover_arr_version(base_url: str, apikey: str, timeout: int,
                          *, session: requests.Session | None = None) -> str | None:
    with arr_session(session) as http:
        for api_path in ('/api/system/status', '/api/v3/system/status'):
            try:
                payload = request_json(f'{base_url}{api_path}?apikey={apikey}', timeout, session=http)
            except requests.exceptions.RequestException:
                continue
            if isinstance(payload, dict):
                version = payload.get('version')
                if isinstance(version, str) and version:
                    return version
    return None
