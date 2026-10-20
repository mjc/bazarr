# coding=utf-8

from collections.abc import Callable
from typing import Literal, TypeVar, overload

import requests

from constants import HEADERS


Decoded = TypeVar('Decoded')


@overload
def open_arr_response(url: str, timeout: int) -> requests.Response: ...


@overload
def open_arr_response(url: str, timeout: int, *, not_found_is_empty: Literal[True]) -> requests.Response | None: ...


def open_arr_response(url: str, timeout: int, *, not_found_is_empty: bool = False) -> requests.Response | None:
    response = requests.get(url, timeout=timeout, verify=False, headers=HEADERS, stream=True)
    if not_found_is_empty and response.status_code == 404:
        response.close()
        return None
    try:
        response.raise_for_status()
    except requests.exceptions.RequestException:
        response.close()
        raise
    return response


def decode_arr_response(response: requests.Response,
                        decoder: Callable[[requests.Response], Decoded]) -> Decoded | None:
    with response:
        if response.status_code != 200:
            return None
        return decoder(response)
