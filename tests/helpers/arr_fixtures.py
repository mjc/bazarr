from sonarr.info import SonarrCapabilities, SonarrConnection
from sonarr.series_snapshot import Series
from utilities.arr import parse_arr_version


def sonarr_connection(version: str = '4.0.9.2421') -> SonarrConnection:
    parsed = parse_arr_version(version)
    assert parsed is not None
    return SonarrConnection('http://sonarr/api/v3/', 'sonarr-key', 30, SonarrCapabilities(parsed))


def series_payload(series_id: int = 123, title: str = 'Series') -> Series:
    return {'id': series_id, 'title': title, 'path': '/series/path', 'tvdbId': series_id,
            'sortTitle': title, 'year': 2024, 'seriesType': 'standard', 'monitored': True,
            'images': [], 'alternateTitles': [], 'tags': []}
