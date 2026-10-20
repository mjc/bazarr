import pytest

from app.language_profiles import Profile, ProfileItem
from subliminal_patch import core
from subtitles.indexer.profile_requirements import calculate_missing_subtitles
from utilities.subtitle_metadata import SubtitleFile


@pytest.mark.parametrize('code', ['xx', 'en-XX', 'not-a-language'])
def test_unknown_stored_subtitle_language_does_not_abort_profile_evaluation(code: str) -> None:
    profile = Profile(1, 'English', None, 'basic', (ProfileItem(1, 'en'),))
    subtitles: list[SubtitleFile] = [{'id': 1, 'path': '/movie.unknown.srt', 'name': None, 'code2': code,
                  'code3': None, 'forced': False, 'hi': False, 'file_size': 123,
                  'embedded_track_id': None}]
    assert calculate_missing_subtitles(profile, subtitles, set(), core._LanguageEquals([]),
                                        use_embedded_subs=True) == ['en']
