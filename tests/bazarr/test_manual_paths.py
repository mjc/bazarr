from tests.helpers.profile_records import profile_record
from types import SimpleNamespace

import pytest

from languages import get_languages
import subtitles.manual as manual
import subtitles.pool as subtitle_pool
import subtitles.utils as subtitle_utils
from constants import HI_EXCLUDED


def _no_op(*args, **kwargs):
    return None


def _record_call(calls):
    def _inner(*args, **kwargs):
        calls.append((args, kwargs))
    return _inner


def _empty_message_result(*args, **kwargs):
    return SimpleNamespace()


@pytest.fixture
def manual_module(bind_wanted_database, monkeypatch):
    bind_wanted_database(manual, "movies")
    bind_wanted_database(manual, "series")
    monkeypatch.setattr(manual.path_mappings, "path_replace", _no_op)
    monkeypatch.setattr(manual.path_mappings, "path_replace_movie", _no_op)
    monkeypatch.setattr(manual, "store_subtitles", _no_op)
    monkeypatch.setattr(manual, "store_subtitles_movie", _no_op)
    monkeypatch.setattr(manual, "history_log", _no_op)
    monkeypatch.setattr(manual, "history_log_movie", _no_op)
    monkeypatch.setattr(manual, "send_notifications", _no_op)
    monkeypatch.setattr(manual, "send_notifications_movie", _no_op)
    monkeypatch.setattr(manual.jobs_queue, "update_job_name", _no_op)
    return manual


@pytest.fixture
def language_dictionary(monkeypatch):
    monkeypatch.setattr(
        get_languages,
        "languages_dict",
        [
            {"code2": "en", "code3": "eng", "code3b": None, "name": "English"},
            {"code2": "fr", "code3": "fra", "code3b": "fre", "name": "French"},
        ],
        raising=False,
    )


@pytest.mark.parametrize("profile_id", [None, 44])
def test_manual_search_rejects_missing_profile_before_provider_setup(monkeypatch, mocker, profile_id):
    monkeypatch.setattr(manual, 'get_profile', lambda profile_id: None)
    pool_update = mocker.patch.object(subtitle_pool, '_update_pool')
    assert manual.manual_search('/movie.mkv', profile_id, [], None, 'Movie', 'movie') == 'Language profile not found or invalid'
    pool_update.assert_not_called()


@pytest.mark.parametrize('flag', [True, 'True'])
def test_get_language_obj_preserves_profile_flags(language_dictionary, flag):
    profile = profile_record({'items': [{'language': 'en', 'forced': flag, 'hi': flag}], 'originalFormat': 1})
    languages, original_format, hi_excluded = manual._get_language_obj(profile)
    assert {(language.forced, language.hi) for language in languages} == {(True, True)}
    assert original_format is True
    assert hi_excluded == set()


@pytest.mark.parametrize('forced', [True, 'True', False, 'False'])
def test_get_language_obj_preserves_hi_exclusion(language_dictionary, forced):
    profile = profile_record({'items': [{'language': 'en', 'forced': forced, 'hi': HI_EXCLUDED}]})
    languages, _, hi_excluded = manual._get_language_obj(profile)
    expected_forced = forced is True or forced == 'True'
    assert {(language.forced, language.hi) for language in languages} == {(expected_forced, False)}
    assert hi_excluded == {('eng', expected_forced)}


@pytest.mark.parametrize('hi', ['True', 'False'])
def test_manual_download_rejects_missing_profile_before_provider_setup(monkeypatch, mocker, hi):
    monkeypatch.setattr(manual, 'get_profile', lambda profile_id: None)
    update = mocker.patch.object(subtitle_pool, '_update_pool')
    assert manual.manual_download_subtitle('/video.mkv', [], hi, 'False', 'cached', 'provider', None, 'Video',
                                          'movie', False, 999) == 'Language profile not found or invalid'
    update.assert_not_called()


def test_episode_manual_download_handles_none_audio_list_and_message_less_result(
    manual_module, show_row_factory, episode_row_factory, monkeypatch
):
    show_row_factory(sonarrSeriesId=5, title="Series", profileId=44)
    episode_row_factory(sonarrSeriesId=5, sonarrEpisodeId=11, title="Pilot", season=1, episode=2)
    notifications = []

    monkeypatch.setattr(manual_module.settings.general, "dont_notify_manual_actions", False)
    monkeypatch.setattr(manual_module, "get_audio_profile_languages", _no_op)
    monkeypatch.setattr(manual_module, "manual_download_subtitle", _empty_message_result)
    monkeypatch.setattr(manual_module, "send_notifications", _record_call(notifications))

    result = manual_module.episode_manually_download_specific_subtitle(
        sonarr_series_id=5,
        sonarr_episode_id=11,
        hi="False",
        forced="False",
        use_original_format="False",
        selected_provider="provider",
        subtitle="sub-id",
        job_id="job",
    )

    assert result == ("", 204)
    assert notifications == []


def test_movie_manual_download_handles_none_audio_list_and_message_less_result(
    manual_module, movie_row_factory, monkeypatch
):
    movie_row_factory(radarrId=7)
    notifications = []

    monkeypatch.setattr(manual_module.settings.general, "dont_notify_manual_actions", False)
    monkeypatch.setattr(manual_module, "get_audio_profile_languages", _no_op)
    monkeypatch.setattr(manual_module, "manual_download_subtitle", _empty_message_result)
    monkeypatch.setattr(manual_module, "send_notifications_movie", _record_call(notifications))

    result = manual_module.movie_manually_download_specific_subtitle(
        radarr_id=7,
        hi="False",
        forced="False",
        use_original_format="False",
        selected_provider="provider",
        subtitle="sub-id",
        job_id="job",
    )

    assert result == ("", 204)
    assert notifications == []


def test_get_video_skips_scene_name_refinement_for_none(monkeypatch):
    calls = []
    video = SimpleNamespace()

    def fake_parse_video(path, **kwargs):
        calls.append((path, kwargs))
        return video

    monkeypatch.setattr(subtitle_utils, "parse_video", fake_parse_video)
    monkeypatch.setattr(subtitle_utils, "registered_refiners", {})

    assert subtitle_utils.get_video("/media/movie.mkv", "Movie", None) is video
    assert [call[0] for call in calls] == ["/media/movie.mkv"]
