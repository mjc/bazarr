import pytest

from radarr.movie_snapshot import Movie, MovieTitle
from radarr.sync.parser import map_movie
from radarr.sync.utils import MOVIE_DECODER
from sonarr.sync.parser import map_episode, map_series
from utilities.arr_types import KEEP_PROFILE, ProfileAssignment
from tests.helpers.arr_fixtures import episode_payload, episode_record, movie_payload, series_payload, series_record


@pytest.mark.parametrize('profile', [KEEP_PROFILE, None, 0, 7])
@pytest.mark.parametrize(('audio', 'expected'), [
    ('None', 'None'),
    ('[]', '[]'),
    ("['English', None, 'English']", "['English', None, 'English']"),
    ("['French']", "['French']"),
])
def test_mapping_uses_resolved_audio_and_tags(
    audio: str, expected: str, profile: ProfileAssignment,
) -> None:
    movie = MOVIE_DECODER.complete_from_value(movie_payload())
    assert isinstance(movie, Movie) and movie.movieFile is not None
    show = series_record(series_payload())
    episode = episode_record(episode_payload())
    assert episode.episodeFile is not None
    tags = ['Resolved', 'Resolved']

    movie_values = map_movie(movie, movie.movieFile, audio_language=audio, tags=tags, profile_id=profile)
    show_values = map_series(show, audio_language=audio, tags=tags, profile_id=profile)
    episode_values = map_episode(episode, episode.episodeFile, audio_language=audio)

    assert movie_values['audio_language'] == expected
    assert show_values['audio_language'] == expected
    assert episode_values['audio_language'] == expected
    assert movie_values['tags'] == "['Resolved', 'Resolved']"
    assert show_values['tags'] == "['Resolved', 'Resolved']"
    for values in (movie_values, show_values):
        if profile is KEEP_PROFILE:
            assert 'profileId' not in values
        else:
            assert 'profileId' in values
            assert values['profileId'] == profile


@pytest.mark.parametrize('legacy', [False, True])
def test_movie_mapping_uses_normalized_titles(legacy: bool) -> None:
    movie = MOVIE_DECODER.complete_from_value(movie_payload())
    assert isinstance(movie, Movie) and movie.movieFile is not None
    movie.alternativeTitles = [MovieTitle('Legacy title')]
    movie.alternateTitles = [MovieTitle('Current title')]

    movie.normalize(legacy=legacy)
    values = map_movie(movie, movie.movieFile, audio_language='[]', tags=[])

    assert values['alternativeTitles'] == ("['Legacy title']" if legacy else "['Current title']")
