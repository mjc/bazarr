from collections.abc import Iterator
import gzip
from io import BytesIO
import json

import pytest
import requests
from urllib3.exceptions import DecodeError, ProtocolError, ReadTimeoutError, SSLError
from urllib3.response import HTTPResponse

from utilities.arr import decode_arr_response
from utilities.json_types import JsonValue


def make_response(body: bytes, encoding: str | None = 'utf-8', *, compressed: bool = False) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.encoding = encoding
    headers = {'Content-Encoding': 'gzip'} if compressed else {}
    response.raw = HTTPResponse(body=BytesIO(gzip.compress(body) if compressed else body),
                                headers=headers, preload_content=False, decode_content=False)
    return response


@pytest.mark.parametrize('payload', [None, True, 7, 1.25, 'subtitle', [], {},
                                    {'id': 2**100, 'text': 'Français 字幕 😀', 'nested': [None, False, {'id': 3}]}])
@pytest.mark.parametrize('encoding', ['utf-8', 'UTF-8', 'utf8'])
@pytest.mark.parametrize('compressed', [False, True])
def test_streamed_utf8_matches_requests_json(payload: JsonValue, encoding: str, compressed: bool) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    with make_response(body, encoding, compressed=compressed) as response:
        assert decode_arr_response(response) == payload
        assert response._content is False


def test_utf8_characters_split_across_chunks_decode_correctly() -> None:
    payload = {'text': 'a' * (10 * 1024 - 11) + '😀字幕'}
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    with make_response(body) as response:
        assert decode_arr_response(response) == payload
        assert response._content is False


@pytest.mark.parametrize('body,encoding', [
    (b'{"text":"\xff"}', 'utf-8'),
    ('{"text":"Français"}'.encode('latin-1'), 'latin-1'),
    ('{"text":"字幕"}'.encode('utf-16'), 'utf-16'),
    ('{"text":"字幕"}'.encode('utf-32'), None),
    (b'\xef\xbb\xbf{"text":"bom"}', None),
    (b'{"text":"fallback"}', 'unknown-codec'),
])
def test_existing_encoding_behavior_is_preserved(body: bytes, encoding: str | None) -> None:
    with make_response(body, encoding) as baseline:
        expected = baseline.json()
    with make_response(body, encoding) as response:
        assert decode_arr_response(response) == expected


@pytest.mark.parametrize('body', [b'', b'{', b'{"a":]}', b'\xef\xbb\xbf{}'])
def test_malformed_json_keeps_the_requests_exception_details(body: bytes) -> None:
    with make_response(body) as baseline:
        with pytest.raises(requests.JSONDecodeError) as original:
            baseline.json()
    with make_response(body) as response:
        with pytest.raises(requests.JSONDecodeError) as actual:
            decode_arr_response(response)
    assert (actual.value.msg, actual.value.doc, actual.value.pos) == (
        original.value.msg, original.value.doc, original.value.pos)


@pytest.mark.parametrize('error,expected', [
    (ProtocolError('partial body'), requests.exceptions.ChunkedEncodingError),
    (DecodeError('corrupt gzip'), requests.exceptions.ContentDecodingError),
    (ReadTimeoutError(None, 'http://arr/', 'timeout'), requests.ConnectionError),
    (SSLError('read failed'), requests.exceptions.SSLError),
])
def test_stream_errors_keep_requests_exception_types(
    error: Exception, expected: type[requests.RequestException], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_stream(amt: int | None = 10 * 1024, decode_content: bool | None = None) -> Iterator[bytes]:
        yield b'{"items":['
        raise error

    with make_response(b'') as response:
        monkeypatch.setattr(response.raw, 'stream', broken_stream)
        with pytest.raises(expected):
            decode_arr_response(response)
