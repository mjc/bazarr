from datetime import datetime

import pytest
from dateutil import parser as date_parser
from dynaconf.utils.boxing import DynaBox

from sonarr.sync import parser
from tests.helpers.arr_fixtures import series_payload, series_record, sonarr_connection


@pytest.mark.parametrize('value', [
    '2024-05-06T12:34:56Z',
    '2024-02-29T23:59:59.1234567Z',
    '2024-05-06T00:15:00+14:00',
    '2024-05-06T23:45:00-12:00',
    '2024-05-06T12:34:56',
    '2024-05-06T12:34:56.123456789+00:00',
    '0001-01-01T00:00:00Z',
    '9999-12-31T23:59:59Z',
    '2024-05-06T12:34:56+01:99',
])
def test_standard_last_aired_avoids_general_date_parser(
    value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = date_parser.parse(value)

    def unexpected_parse(value: str) -> datetime:
        pytest.fail('Standard timestamps should use native datetime parsing')

    monkeypatch.setattr(parser.parser, 'parse', unexpected_parse)
    actual = parser._parse_last_aired(value)
    assert actual == expected
    assert actual.strftime('%Y-%m-%d') == expected.strftime('%Y-%m-%d')


@pytest.mark.parametrize('value', [
    'May 6, 2024 12:34:56',
    '2024-05-06',
    '20240506T123456Z',
    '2024-05-06 12:34:56+02:00',
    '2024-05-06T12:34:56,123Z',
    '2024-05-06T12:34:56+24:00',
])
def test_other_last_aired_formats_keep_general_parser_behavior(
    value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = date_parser.parse
    expected = original(value)
    calls: list[str] = []

    def parse(value: str) -> datetime:
        calls.append(value)
        result = original(value)
        assert isinstance(result, datetime)
        return result

    monkeypatch.setattr(parser.parser, 'parse', parse)
    actual = parser._parse_last_aired(value)
    assert calls == [value]
    assert actual.replace(tzinfo=None) == expected.replace(tzinfo=None)
    assert actual.tzinfo == expected.tzinfo
    assert actual.strftime('%Y-%m-%d') == expected.strftime('%Y-%m-%d')


@pytest.mark.parametrize('value', [
    'not a date', '2024-02-30T12:34:56Z', '2024-05-06T25:00:00Z',
    '2024-W01-1T12:34:56',
])
def test_invalid_last_aired_keeps_general_parser_failure(value: str) -> None:
    with pytest.raises(ValueError) as expected:
        original = date_parser.parse(value)
        original.strftime('%Y-%m-%d')
    with pytest.raises(type(expected.value)):
        parser._parse_last_aired(value).strftime('%Y-%m-%d')


@pytest.mark.parametrize('value', [None, '', '2024-05-06T00:15:00+14:00', 'May 6, 2024 12:34:56'])
def test_series_parser_preserves_last_aired_date(value: str | None) -> None:
    payload = series_payload()
    payload['lastAired'] = value
    show = series_record(payload)
    general = DynaBox({'parse_embedded_audio_track': False, 'serie_tag_enabled': False})
    result = parser.seriesParser(show, 'update', [], [], None, [], general=general,
                                 capabilities=sonarr_connection().capabilities)
    assert result['lastAired'] == ('2024-05-06' if value else None)
