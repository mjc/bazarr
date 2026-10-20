from collections.abc import Callable, Sequence
from types import ModuleType
from typing import ParamSpec

import msgspec
import pytest

from radarr.movie_snapshot import Movie
from sonarr.episode_snapshot import Episode, EpisodeFile
from sonarr.series_snapshot import Series
from utilities.arr_decoding import InvalidArrRecord
from utilities.json_types import JsonValue
from radarr.info import RadarrConnection
from sonarr.info import SonarrCapabilities, SonarrConnection
from utilities.arr import parse_arr_version


def sonarr_connection(version: str = '4.0.9.2421') -> SonarrConnection:
    parsed = parse_arr_version(version)
    assert parsed is not None
    return SonarrConnection('http://sonarr/api/v3/', 'sonarr-key', 30, SonarrCapabilities(parsed))


def series_payload(series_id: int = 123, title: str = 'Series') -> dict[str, JsonValue]:
    return {'id': series_id, 'title': title, 'path': '/series/path', 'tvdbId': series_id,
            'sortTitle': title, 'year': 2024, 'seriesType': 'standard', 'monitored': True,
            'images': [], 'alternateTitles': [], 'tags': []}


def radarr_connection(version: str = '5.0.0.1') -> RadarrConnection:
    from radarr.info import RadarrCapabilities, RadarrConnection
    parsed = parse_arr_version(version)
    assert parsed is not None
    return RadarrConnection('http://radarr/api/v3/', 'radarr-key', 30, RadarrCapabilities(parsed))


def movie_payload(movie_id: int = 1, title: str = 'Movie', path: str = '/movies/movie.mkv',
                  file_id: int = 10, size: int = 999999999) -> dict[str, JsonValue]:
    return {'id': movie_id, 'title': title, 'tmdbId': movie_id, 'year': 2024, 'sortTitle': title,
            'monitored': True, 'hasFile': True, 'tags': [], 'images': [],
            'movieFile': {'id': file_id, 'path': path, 'size': size,
                          'quality': {'quality': {'name': 'HDTV-1080p'}}, 'languages': [{'name': 'English'}]}}


def episode_payload(series_id: int = 7, episode_id: int = 101) -> dict[str, JsonValue]:
    return {'id': episode_id, 'seriesId': series_id, 'title': 'Pilot', 'monitored': True, 'hasFile': True,
            'seasonNumber': 1, 'episodeNumber': 1, 'episodeFileId': 11,
            'episodeFile': {'id': 11, 'path': '/show/episode.mkv', 'size': 999999999,
                            'quality': {'quality': {'name': 'HDTV-1080p'}}, 'language': {'name': 'English'}}}


Params = ParamSpec('Params')


def stub_arr_api(monkeypatch: pytest.MonkeyPatch, module: ModuleType, name: str,
                 fetch: Callable[Params, JsonValue]) -> None:
    """Keep wire-payload fixtures at the same validation boundary as HTTP reads."""
    from functools import wraps
    from radarr.sync.utils import MOVIE_DECODER
    from sonarr.sync.utils import SERIES_DECODER, EPISODE_DECODER, EPISODEFILE_DECODER

    decode = {
        'get_movies_from_radarr_api': MOVIE_DECODER.from_value,
        'get_series_from_sonarr_api': SERIES_DECODER.from_value,
        'get_episodes_from_sonarr_api': EPISODE_DECODER.complete_from_value,
        'get_episodesFiles_from_sonarr_api': EPISODEFILE_DECODER.complete_from_value,
    }[name]

    @wraps(fetch)
    def response(*args: Params.args, **kwargs: Params.kwargs) -> (
        msgspec.Struct | Sequence[msgspec.Struct | InvalidArrRecord] | InvalidArrRecord | None
    ):
        return decode(fetch(*args, **kwargs))

    monkeypatch.setattr(module, name, response)


def episode_record(value: JsonValue) -> Episode:
    from sonarr.sync.utils import EPISODE_DECODER
    record = EPISODE_DECODER.complete_from_value(value)
    assert isinstance(record, Episode)
    return record


def episode_file_record(value: JsonValue) -> EpisodeFile:
    from sonarr.sync.utils import EPISODEFILE_DECODER
    record = EPISODEFILE_DECODER.complete_from_value(value)
    assert isinstance(record, EpisodeFile)
    return record


def movie_record(value: JsonValue) -> Movie:
    from radarr.sync.utils import MOVIE_DECODER
    record = MOVIE_DECODER.complete_from_value(value)
    assert isinstance(record, Movie)
    return record


def series_record(value: JsonValue) -> Series:
    from sonarr.sync.utils import SERIES_DECODER
    record = SERIES_DECODER.complete_from_value(value)
    assert isinstance(record, Series)
    return record
