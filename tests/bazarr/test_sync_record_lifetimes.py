from weakref import ReferenceType, ref

import pytest
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from radarr.movie_snapshot import Movie
from radarr.sync import movies
from sonarr.episode_snapshot import Episode
from sonarr.series_snapshot import Series
from sonarr.sync import episodes, series
from tests.bazarr.test_arr_sync_state import configure_series
from tests.bazarr.test_sync_lookups import configure_episodes, configure_movies
from tests.helpers.arr_fixtures import episode_payload, movie_payload, series_payload


class TrackedMovie(Movie):
    pass


class TrackedEpisode(Episode):
    pass


class TrackedSeries(Series):
    pass


def test_movie_records_are_released_before_indexing(memory_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_movies(monkeypatch, memory_session, 1)
    references: list[ReferenceType[Movie]] = []
    indexed: list[int] = []

    def fetch(*, apikey_radarr: str) -> list[Movie]:
        record = TypeAdapter(TrackedMovie).validate_python(movie_payload(path='/changed.mkv', file_id=2001))
        references.append(ref(record))
        return [record]

    def index(identifier: int) -> None:
        assert all(reference() is None for reference in references)
        indexed.append(identifier)

    monkeypatch.setattr(movies, 'get_movies_from_radarr_api', fetch)
    monkeypatch.setattr(movies, 'store_subtitles_movie', index)
    movies.update_movies(job_id=1)
    assert indexed == [1]


@pytest.mark.parametrize('selected', [False, True])
def test_episode_records_are_released_before_indexing(
    selected: bool, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_episodes(monkeypatch, memory_session, 1)
    references: list[ReferenceType[Episode]] = []
    indexed: list[int] = []

    def fetch(*, apikey_sonarr: str, series_id: int | None = None,
              episode_id: int | None = None) -> list[Episode]:
        payload = episode_payload(episode_id=1)
        file = payload['episodeFile']
        assert isinstance(file, dict)
        file['id'] = 2001
        file['path'] = '/changed.mkv'
        record = TypeAdapter(TrackedEpisode).validate_python(payload)
        references.append(ref(record))
        return [record]

    def index(identifier: int) -> None:
        assert all(reference() is None for reference in references)
        indexed.append(identifier)

    monkeypatch.setattr(episodes, 'get_episodes_from_sonarr_api', fetch)
    monkeypatch.setattr(episodes, 'store_subtitles', index)
    if selected:
        episodes.sync_episodes(episode_ids=[1], defer_search=True)
    else:
        episodes.sync_episodes(7, defer_search=True)
    assert indexed == [1]


@pytest.mark.parametrize('count', [2, 100, 205])
def test_processed_series_records_are_released_before_episode_sync(
    count: int, memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_series(monkeypatch, memory_session, count)
    references: list[ReferenceType[Series]] = []
    synchronized: list[int] = []

    def fetch(*, apikey_sonarr: str) -> list[Series]:
        records: list[Series] = [TypeAdapter(TrackedSeries).validate_python(series_payload(series_id=identifier))
                                 for identifier in range(1, count + 1)]
        for record in records:
            record.path = f'/show/{record.id}'
        references.extend(ref(record) for record in records)
        return records

    def sync(series_id: int) -> None:
        assert references[series_id - 1]() is None
        synchronized.append(series_id)

    monkeypatch.setattr(series, 'get_series_from_sonarr_api', fetch)
    monkeypatch.setattr(series, 'sync_episodes', sync)
    series.update_series(job_id=1)
    assert synchronized == list(range(1, count + 1))
