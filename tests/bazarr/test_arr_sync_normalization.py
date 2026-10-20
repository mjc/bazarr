import json
from unittest.mock import Mock

import pytest

from radarr.movie_snapshot import IncompleteMovie, Movie
from radarr.sync import audio as movie_audio, utils as radarr
from radarr.sync.parser import map_movie
from sonarr.series_snapshot import Series
from sonarr.sync import utils as sonarr
from sonarr.sync.parser import map_series
from tests.bazarr.test_arr_typed_decoding import make_response
from tests.helpers.arr_fixtures import movie_payload, series_payload
from utilities.arr_sync_lookups import ArrSyncLookups


def language_name(name: str | None) -> str | None:
    return name


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('single', [False, True])
def test_movie_response_normalizes_version_fields_before_mapping(
    legacy: bool, single: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = movie_payload()
    payload['qualityProfileId'] = 7
    payload['alternativeTitles'] = [{'title': 'Legacy'}]
    payload['alternateTitles'] = [{'title': 'Current'}]
    file = payload['movieFile']
    assert isinstance(file, dict)
    file['languages'] = [{'name': 'French'}]
    file['mediaInfo'] = {
        'videoFormat': 'AVC', 'videoCodec': 'HEVC',
        'audioFormat': 'AC-3', 'audioCodec': 'AAC', 'audioLanguages': ' English / French ',
    }
    response = make_response(json.dumps(payload if single else [payload, {'id': 2, 'hasFile': False}]).encode())
    request = Mock(return_value=response)
    monkeypatch.setattr('requests.get', request)
    monkeypatch.setattr(radarr, 'url_api_radarr', lambda: 'http://example.invalid/api/' + ('' if legacy else 'v3/'))
    monkeypatch.setattr(movie_audio, 'audio_language_from_name', language_name)

    records = radarr.get_movies_from_radarr_api('key', radarr_id=1 if single else None)

    assert records is not None
    movie = records[0]
    assert isinstance(movie, Movie) and movie.movieFile is not None
    media = movie.movieFile.mediaInfo
    assert media is not None
    assert media.videoCodec == ('AVC' if legacy else 'HEVC')
    assert media.audioCodec == ('AC-3' if legacy else 'AAC')
    assert movie.qualityProfileId == (7 if legacy else None)
    lookups = ArrSyncLookups([], [], [(7, 'Fallback')])
    audio = movie_audio.movie_audio_language(movie, movie.movieFile, lookups, parse_embedded_audio_track=False)
    values = map_movie(movie, movie.movieFile, audio_language=audio, tags=[])
    assert values['alternativeTitles'] == ("['Legacy']" if legacy else "['Current']")
    assert values['audio_language'] == ("['English', 'French']" if legacy else "['French']")
    movie.normalize(legacy=legacy)
    assert map_movie(movie, movie.movieFile, audio_language=audio, tags=[]) == values
    if not single:
        assert isinstance(records[1], IncompleteMovie)
        assert records[1].id == 2 and records[1].hasFile is False
    assert response.raw.closed
    request.assert_called_once()


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('single', [False, True])
def test_series_response_selects_audio_profile_once_at_the_boundary(
    legacy: bool, single: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = series_payload(series_id=1)
    payload['qualityProfileId'] = 7
    payload['languageProfileId'] = 8
    response = make_response(json.dumps(payload if single else [payload, {'id': 2}]).encode())
    monkeypatch.setattr('requests.get', Mock(return_value=response))
    monkeypatch.setattr(sonarr, 'url_api_sonarr', lambda: 'http://example.invalid/api/' + ('' if legacy else 'v3/'))

    records = sonarr.get_series_from_sonarr_api('key', sonarr_series_id=1 if single else None)

    assert records is not None
    show = records[0]
    assert isinstance(show, Series)
    assert show.languageProfileId == (7 if legacy else 8)
    lookups = ArrSyncLookups([], [], [(7, 'English'), (8, 'French')])
    audio = lookups.audio_language_values.get(show.languageProfileId, '[]')
    assert audio is lookups.audio_language_values.get(show.languageProfileId, '[]')
    assert map_series(show, audio_language=audio, tags=[])['audio_language'] == (
        "['English']" if legacy else "['French']")
    if not single:
        assert records[1] is None
    assert response.raw.closed


def test_movie_normalization_reuses_decoded_records_and_title_lists() -> None:
    payload = movie_payload()
    payload['alternativeTitles'] = [{'title': 'Legacy'}]
    movie = radarr.MOVIE_DECODER.complete_from_value(payload)
    assert isinstance(movie, Movie)
    file = movie.movieFile
    titles = movie.alternativeTitles

    movie.normalize(legacy=True)

    assert movie.movieFile is file
    assert movie.alternateTitles is titles


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('media_present', [False, True])
@pytest.mark.parametrize('raw_audio', [None, '', ' English / French '])
@pytest.mark.parametrize('languages', [None, [], ['French', None, 'French']])
def test_normalized_audio_preserves_version_precedence_and_empty_values(
    legacy: bool, media_present: bool, raw_audio: str | None, languages: list[str | None] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = movie_payload()
    payload['qualityProfileId'] = 7
    file = payload['movieFile']
    assert isinstance(file, dict)
    file['mediaInfo'] = {'audioLanguages': raw_audio} if media_present else None
    file['languages'] = [{'name': name} for name in languages] if languages is not None else None
    movie = radarr.MOVIE_DECODER.complete_from_value(payload)
    assert isinstance(movie, Movie) and movie.movieFile is not None
    monkeypatch.setattr(movie_audio, 'audio_language_from_name', language_name)
    lookups = ArrSyncLookups([], [], [(7, 'Fallback')])

    movie.normalize(legacy=legacy)
    audio = movie_audio.movie_audio_language(movie, movie.movieFile, lookups, parse_embedded_audio_track=False)

    if legacy:
        expected = (str([name.strip() for name in raw_audio.split('/')])
                    if media_present and raw_audio is not None else "['Fallback']")
    else:
        expected = str(languages) if languages else '[]'
    assert audio == expected
