# coding=utf-8

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar, overload

import requests
from requests.compat import JSONDecodeError, json
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


def decode_arr_response(response: requests.Response) -> JsonValue:
    if response.encoding is None or response.encoding.lower() not in ('utf-8', 'utf8'):
        payload: JsonValue = response.json()
        return payload
    # Keep the byte body out of the response cache so it is released before
    # parsing. Requests handles decompression and transport errors.
    text = b''.join(response.iter_content(chunk_size=10 * 1024)).decode('utf-8', errors='replace')
    try:
        payload = json.loads(text)
    except JSONDecodeError as error:
        raise requests.exceptions.JSONDecodeError(error.msg, error.doc, error.pos) from error
    return payload


Result = TypeVar('Result')


@overload
def request_json(url: str, timeout: int, *, session: requests.Session | None = None) -> JsonValue: ...


@overload
def request_json(url: str, timeout: int, *, decoder: Callable[[requests.Response], Result],
                 session: requests.Session | None = None) -> Result: ...


def request_json(url: str, timeout: int, *, session: requests.Session | None = None,
                 decoder: Callable[[requests.Response], Result] | None = None) -> JsonValue | Result:
    with arr_session(session) as http:
        with http.get(url, timeout=timeout, verify=False, headers=HEADERS, stream=True) as response:
            response.raise_for_status()
            return decode_arr_response(response) if decoder is None else decoder(response)


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
