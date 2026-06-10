from types import SimpleNamespace

import pytest

import subtitles.manual as manual


class _Language:
    def __init__(self, code, hi=False, forced=False):
        self.code = code
        self.hi = hi
        self.forced = forced

    def __hash__(self):
        return hash((self.code, self.hi, self.forced))

    def __eq__(self, other):
        return isinstance(other, _Language) and (self.code, self.hi, self.forced) == (
            other.code,
            other.hi,
            other.forced,
        )


@pytest.fixture
def manual_module(bind_wanted_database, monkeypatch):
    bind_wanted_database(manual, "movies")
    bind_wanted_database(manual, "series")
    monkeypatch.setattr(manual.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(manual.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(manual, "get_profile_id", lambda **kwargs: 44)
    monkeypatch.setattr(manual, "store_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(manual, "store_subtitles_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(manual, "history_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(manual, "history_log_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(manual, "send_notifications", lambda *args, **kwargs: None)
    monkeypatch.setattr(manual, "send_notifications_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(manual.jobs_queue, "update_job_name", lambda **kwargs: None)
    return manual


def test_get_language_obj_handles_missing_profile_payload(monkeypatch):
    monkeypatch.setattr(manual, "get_profiles_list", lambda profile_id: None)

    language_set, original_format = manual._get_language_obj(profile_id=44)

    assert language_set == set()
    assert original_format is False


def test_get_language_obj_handles_malformed_profile_items(monkeypatch):
    monkeypatch.setattr(manual, "alpha3_from_alpha2", lambda code: f"{code}3")
    monkeypatch.setattr(manual, "_get_lang_obj", lambda code: _Language(code))
    monkeypatch.setattr(
        manual,
        "Language",
        SimpleNamespace(rebuild=lambda lang, hi=False, forced=False: _Language(lang.code, hi=hi, forced=forced)),
    )
    monkeypatch.setattr(
        manual,
        "get_profiles_list",
        lambda profile_id: {
            "items": [
                None,
                {"bad": "shape"},
                {"language": None},
                {"language": "en", "forced": "True", "hi": "False"},
                {"language": "fr", "forced": None, "hi": "True"},
            ],
            "originalFormat": 1,
        },
    )

    language_set, original_format = manual._get_language_obj(profile_id=44)

    assert len(language_set) == 2
    assert original_format == 1


def test_get_language_obj_handles_non_integer_profile_id(monkeypatch):
    monkeypatch.setattr(manual, "get_profiles_list", lambda profile_id: {"items": [], "originalFormat": 1})

    language_set, original_format = manual._get_language_obj(profile_id="not-an-int")

    assert language_set == set()
    assert original_format is False


def test_episode_manual_download_handles_none_audio_list_and_message_less_result(
    manual_module, show_row_factory, episode_row_factory, monkeypatch
):
    show_row_factory(sonarrSeriesId=5, title="Series", profileId=44)
    episode_row_factory(sonarrSeriesId=5, sonarrEpisodeId=11, title="Pilot", season=1, episode=2)
    notifications = []

    monkeypatch.setattr(manual_module.settings.general, "dont_notify_manual_actions", False)
    monkeypatch.setattr(manual_module, "get_audio_profile_languages", lambda audio_language: None)
    monkeypatch.setattr(manual_module, "manual_download_subtitle", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        manual_module,
        "send_notifications",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

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
    monkeypatch.setattr(manual_module, "get_audio_profile_languages", lambda audio_language: None)
    monkeypatch.setattr(manual_module, "manual_download_subtitle", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        manual_module,
        "send_notifications_movie",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

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


def test_episode_manual_download_passes_none_for_missing_scene_name(
    manual_module, show_row_factory, episode_row_factory, monkeypatch
):
    show_row_factory(sonarrSeriesId=5, title="Series", profileId=44)
    episode_row_factory(sonarrSeriesId=5, sonarrEpisodeId=11, title="Pilot", sceneName=None)
    captured_scenes = []

    monkeypatch.setattr(manual_module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(
        manual_module,
        "manual_download_subtitle",
        lambda *args, **kwargs: captured_scenes.append(args[6]) or SimpleNamespace(),
    )

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
    assert captured_scenes == [None]


def test_episode_manual_download_handles_noninteger_episode_numbers(
    manual_module, show_row_factory, episode_row_factory, monkeypatch
):
    show_row_factory(sonarrSeriesId=5, title="Series", profileId=44)
    episode_row_factory(
        sonarrSeriesId=5,
        sonarrEpisodeId=11,
        title="Pilot",
        season="special",
        episode=None,
        episodeTitle="Oddball",
    )
    names = []

    monkeypatch.setattr(manual_module.jobs_queue, "update_job_name", lambda **kwargs: names.append(kwargs["new_job_name"]))
    monkeypatch.setattr(manual_module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(manual_module, "manual_download_subtitle", lambda *args, **kwargs: SimpleNamespace())

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
    assert names == [
        "Manually downloading Subtitles for Series - SspecialE?? - Oddball",
        "Manually downloaded Subtitles for Series - SspecialE?? - Oddball",
    ]


def test_movie_manual_download_passes_none_for_missing_scene_name(
    manual_module, movie_row_factory, monkeypatch
):
    movie_row_factory(radarrId=7, sceneName=None)
    captured_scenes = []

    monkeypatch.setattr(manual_module, "get_audio_profile_languages", lambda audio_language: [{"name": "English"}])
    monkeypatch.setattr(
        manual_module,
        "manual_download_subtitle",
        lambda *args, **kwargs: captured_scenes.append(args[6]) or SimpleNamespace(),
    )

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
    assert captured_scenes == [None]
