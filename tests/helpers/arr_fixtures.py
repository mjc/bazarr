from radarr.info import RadarrConnection
from radarr.movie_snapshot import Movie
from sonarr.episode_snapshot import Episode
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


def radarr_connection(version: str = '5.0.0.1') -> RadarrConnection:
    from radarr.info import RadarrCapabilities, RadarrConnection
    parsed = parse_arr_version(version)
    assert parsed is not None
    return RadarrConnection('http://radarr/api/v3/', 'radarr-key', 30, RadarrCapabilities(parsed))


def movie_payload(movie_id: int = 1, title: str = 'Movie', path: str = '/movies/movie.mkv',
                  file_id: int = 10, size: int = 999999999) -> Movie:
    return {'id': movie_id, 'title': title, 'tmdbId': movie_id, 'year': 2024, 'sortTitle': title,
            'monitored': True, 'hasFile': True, 'tags': [], 'images': [],
            'movieFile': {'id': file_id, 'path': path, 'size': size,
                          'quality': {'quality': {'name': 'HDTV-1080p'}}, 'languages': [{'name': 'English'}]}}


def episode_payload(series_id: int = 7, episode_id: int = 101) -> Episode:
    return {'id': episode_id, 'seriesId': series_id, 'title': 'Pilot', 'monitored': True, 'hasFile': True,
            'seasonNumber': 1, 'episodeNumber': 1, 'episodeFileId': 11,
            'episodeFile': {'id': 11, 'path': '/show/episode.mkv', 'size': 999999999,
                            'quality': {'quality': {'name': 'HDTV-1080p'}}, 'language': {'name': 'English'}}}
