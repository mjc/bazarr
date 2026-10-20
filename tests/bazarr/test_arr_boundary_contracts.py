import json
from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import TableMovies
from radarr.movie_snapshot import IncompleteMovie, Movie
from radarr.sync import utils as radarr
from sonarr.episode_snapshot import Episode, EpisodeFile, complete_episode_snapshot
from sonarr.series_snapshot import Series
from sonarr.sync import utils as sonarr
from tests.bazarr.test_arr_typed_decoding import make_response
from tests.bazarr.test_sync_lookups import configure_movies
from tests.helpers.arr_fixtures import episode_payload, episode_record, movie_payload, series_payload
from utilities.json_types import JsonValue


def fetch_media(kind: str, single: bool) -> Sequence[Movie | IncompleteMovie | Series | Episode | EpisodeFile | None] | None:
    if kind == 'movies':
        return radarr.get_movies_from_radarr_api('key', 1 if single else None)
    if kind == 'series':
        return sonarr.get_series_from_sonarr_api('key', 123 if single else None)
    if kind == 'episodes':
        return sonarr.get_episodes_from_sonarr_api('key', episode_id=101 if single else None,
                                                  series_id=None if single else 7)
    return sonarr.get_episodesFiles_from_sonarr_api('key', episode_file_id=11 if single else None,
                                                   series_id=None if single else 7)


@pytest.mark.parametrize('kind', ['movies', 'series', 'episodes', 'files'])
@pytest.mark.parametrize('single', [False, True])
@pytest.mark.parametrize('wrong', ['shape', 'identity', 'null'])
def test_fetch_rejects_wrong_shape_or_identity(kind: str, single: bool, wrong: str,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {'movies': movie_payload(), 'series': series_payload(), 'episodes': episode_payload(),
               'files': episode_payload()['episodeFile']}[kind]
    assert isinstance(payload, dict)
    if wrong == 'identity':
        payload['id'] = 999 if single else 0
    wire: JsonValue = [payload] if not single else payload
    if wrong == 'shape':
        wire = payload if not single else [payload]
    elif wrong == 'null':
        wire = None
    monkeypatch.setattr(radarr, 'url_api_radarr', lambda: 'http://example.invalid/api/v3/')
    monkeypatch.setattr(sonarr, 'url_api_sonarr', lambda: 'http://example.invalid/api/v3/')
    response = make_response(json.dumps(wire).encode())
    monkeypatch.setattr(radarr.requests, 'get', lambda *args, **kwargs: response)
    monkeypatch.setattr(sonarr.get_sonarr_info, 'semver', lambda: pytest.fail('Reject before file completion'))

    result = fetch_media(kind, single)

    if wrong == 'identity' and not single and kind in ('series', 'movies'):
        assert result is not None and len(result) == 1
        if kind == 'movies':
            assert isinstance(result[0], IncompleteMovie) and result[0].id is None
        else:
            assert result == [None]
    else:
        assert result is None
    assert response.raw.closed


@pytest.mark.parametrize('kind', ['movies', 'series', 'episodes', 'files'])
def test_empty_catalogue_is_successful(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(radarr, 'url_api_radarr', lambda: 'http://example.invalid/api/v3/')
    monkeypatch.setattr(sonarr, 'url_api_sonarr', lambda: 'http://example.invalid/api/v3/')
    response = make_response(b'[]')
    monkeypatch.setattr(radarr.requests, 'get', lambda *args, **kwargs: response)
    assert fetch_media(kind, False) == []
    assert response.raw.closed


@pytest.mark.parametrize('separate', [False, True])
def test_snapshot_clears_stale_file_when_episode_has_no_file(separate: bool) -> None:
    episode = episode_record(episode_payload())
    file = episode.episodeFile
    assert file is not None
    episode.hasFile = False
    result = complete_episode_snapshot([episode], [file] if separate else (), separate_files=separate)
    assert result == [episode]
    assert episode.episodeFile is None


@pytest.mark.parametrize(('size', 'expected'), [('1000000000', 1000000000), (1000000000.9, 1000000000),
                                               (1000000000, 1000000000)])
def test_movie_size_is_normalized_before_sync(size: JsonValue, expected: int, memory_session: Session,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from radarr.sync import movies
    configure_movies(monkeypatch, memory_session, 1)
    payload = movie_payload(title='Changed size')
    file = payload['movieFile']
    assert isinstance(file, dict)
    file['size'] = size
    decoded = radarr.MOVIE_DECODER.from_value(payload)
    assert isinstance(decoded, Movie) and decoded.movieFile is not None
    assert isinstance(decoded.movieFile.size, int) and decoded.movieFile.size == expected
    monkeypatch.setattr(movies, 'get_movies_from_radarr_api', lambda **kwargs: [decoded])
    monkeypatch.setattr(movies, 'store_subtitles_movie', lambda identifier: None)
    movies.update_movies(job_id=1)
    assert memory_session.execute(select(TableMovies.file_size)).scalar_one() == expected
