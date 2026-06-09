from pathlib import Path
from types import SimpleNamespace

from tests.test_helpers import _Column, _Query, _Result, load_isolated_module


def _load_manual_module(profile_payload):
    root = Path(__file__).resolve().parents[2]
    module_path = root / "bazarr" / "subtitles" / "manual.py"

    class _Language:
        def __init__(self, code, hi=False, forced=False):
            self.code = code
            self.hi = hi
            self.forced = forced

        def __hash__(self):
            return hash((self.code, self.hi, self.forced))

        def __eq__(self, other):
            return (
                isinstance(other, _Language)
                and (self.code, self.hi, self.forced) == (other.code, other.hi, other.forced)
            )

        @staticmethod
        def rebuild(lang, hi=False, forced=False):
            return _Language(lang.code, hi=hi or lang.hi, forced=forced or lang.forced)

    return load_isolated_module(
        "subtitles.manual",
        module_path,
        ["subtitles", "app", "utilities", "languages", "sonarr", "radarr", "bazarr", "bazarr.subtitles"],
        {
            "subliminal": SimpleNamespace(),
            "subzero.language": SimpleNamespace(Language=_Language),
            "subliminal_patch.core": SimpleNamespace(save_subtitles=lambda *args, **kwargs: []),
            "subliminal_patch.core_persistent": SimpleNamespace(
                list_all_subtitles=lambda *args, **kwargs: {},
                download_subtitles=lambda *args, **kwargs: None,
            ),
            "subliminal_patch.score": SimpleNamespace(compute_score=lambda *args, **kwargs: (0, 0), DEFAULT_SCORES={"episode": {}, "movie": {}}),
            "languages.get_languages": SimpleNamespace(alpha3_from_alpha2=lambda code: f"{code}3"),
            "app.config": SimpleNamespace(
                settings=SimpleNamespace(
                    general=SimpleNamespace(
                        minimum_score=0,
                        minimum_score_movie=0,
                        utf8_encode=False,
                        chmod_enabled=False,
                        dont_notify_manual_actions=True,
                        subzero_mods=[],
                    )
                ),
                get_array_from=lambda value: value,
            ),
            "utilities.helper": SimpleNamespace(get_target_folder=lambda path: None, force_unicode=lambda path: path),
            "utilities.path_mappings": SimpleNamespace(path_mappings=SimpleNamespace(path_replace=lambda path: path, path_replace_movie=lambda path: path)),
            "app.database": SimpleNamespace(
                database=SimpleNamespace(execute=lambda stmt: None),
                get_profiles_list=lambda profile_id: profile_payload,
                select=lambda *args, **kwargs: _Query(),
                TableEpisodes=SimpleNamespace(path=_Column("path"), sceneName=_Column("sceneName"), audio_language=_Column("audio_language"), season=_Column("season"), episode=_Column("episode"), title=_Column("title"), sonarrEpisodeId=_Column("sonarrEpisodeId")),
                TableShows=SimpleNamespace(title=_Column("title"), profileId=_Column("profileId")),
                get_audio_profile_languages=lambda audio_language: [],
                get_profile_id=lambda **kwargs: 1,
                TableMovies=SimpleNamespace(title=_Column("title"), year=_Column("year"), path=_Column("path"), sceneName=_Column("sceneName"), audio_language=_Column("audio_language"), radarrId=_Column("radarrId")),
            ),
            "app.jobs_queue": SimpleNamespace(jobs_queue=SimpleNamespace(add_job_from_function=lambda *args, **kwargs: "job", update_job_name=lambda **kwargs: None)),
            "app.notifier": SimpleNamespace(send_notifications=lambda *args, **kwargs: None, send_notifications_movie=lambda *args, **kwargs: None),
            "sonarr.history": SimpleNamespace(history_log=lambda *args, **kwargs: None),
            "radarr.history": SimpleNamespace(history_log_movie=lambda *args, **kwargs: None),
            "subtitles.indexer.series": SimpleNamespace(store_subtitles=lambda *args, **kwargs: None),
            "subtitles.indexer.movies": SimpleNamespace(store_subtitles_movie=lambda *args, **kwargs: None),
            "subtitles.processing": SimpleNamespace(ProcessSubtitlesResult=lambda **kwargs: SimpleNamespace(**kwargs), process_subtitle=lambda **kwargs: None),
            "bazarr.subtitles.cache": SimpleNamespace(subtitle_cache=SimpleNamespace(get=lambda key: None)),
            "subtitles.pool": SimpleNamespace(update_pools=lambda fn: fn, _get_pool=lambda *args, **kwargs: SimpleNamespace()),
            "subtitles.utils": SimpleNamespace(
                get_video=lambda *args, **kwargs: None,
                _get_lang_obj=lambda code: _Language(code),
                _get_scores=lambda *args, **kwargs: (0, 0, set()),
                _set_forced_providers=lambda *args, **kwargs: None,
            ),
        },
    )


def test_get_language_obj_handles_missing_profile_payload():
    module = _load_manual_module(profile_payload=None)

    language_set, original_format = module._get_language_obj(profile_id=44)

    assert language_set == set()
    assert original_format is False


def test_get_language_obj_handles_malformed_profile_items():
    module = _load_manual_module(
        profile_payload={
            "items": [
                None,
                {"bad": "shape"},
                {"language": None},
                {"language": "en", "forced": "True", "hi": "False"},
                {"language": "fr", "forced": None, "hi": "True"},
            ],
            "originalFormat": 1,
        }
    )

    language_set, original_format = module._get_language_obj(profile_id=44)

    assert len(language_set) == 2
    assert original_format == 1


def test_get_language_obj_handles_non_integer_profile_id():
    module = _load_manual_module(profile_payload={"items": [], "originalFormat": 1})

    language_set, original_format = module._get_language_obj(profile_id="not-an-int")

    assert language_set == set()
    assert original_format is False


def test_episode_manual_download_handles_none_audio_list_and_message_less_result():
    module = _load_manual_module(profile_payload={"items": [], "originalFormat": 0})
    module.settings.general.dont_notify_manual_actions = False

    episode_info = SimpleNamespace(
        audio_language="['eng']",
        path="/series/episode.mkv",
        sceneName=None,
        season=1,
        episode=2,
        episodeTitle="Pilot",
        title="Series",
    )
    module.database = SimpleNamespace(execute=lambda stmt: _Result(first_value=episode_info))
    module.get_audio_profile_languages = lambda audio_language: None
    module.path_mappings.path_replace = lambda path: path
    module.get_profile_id = lambda **kwargs: 44
    module.manual_download_subtitle = lambda *args, **kwargs: SimpleNamespace()
    module.store_subtitles = lambda *args, **kwargs: None
    module.history_log = lambda *args, **kwargs: None
    notifications = []
    module.send_notifications = lambda *args, **kwargs: notifications.append((args, kwargs))

    result = module.episode_manually_download_specific_subtitle(
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


def test_movie_manual_download_handles_none_audio_list_and_message_less_result():
    module = _load_manual_module(profile_payload={"items": [], "originalFormat": 0})
    module.settings.general.dont_notify_manual_actions = False

    movie_info = SimpleNamespace(
        title="Movie",
        year=2024,
        path="/movies/movie.mkv",
        sceneName=None,
        audio_language="['eng']",
    )
    module.database = SimpleNamespace(execute=lambda stmt: _Result(first_value=movie_info))
    module.get_audio_profile_languages = lambda audio_language: None
    module.path_mappings.path_replace_movie = lambda path: path
    module.get_profile_id = lambda **kwargs: 44
    module.manual_download_subtitle = lambda *args, **kwargs: SimpleNamespace()
    module.store_subtitles_movie = lambda *args, **kwargs: None
    module.history_log_movie = lambda *args, **kwargs: None
    notifications = []
    module.send_notifications_movie = lambda *args, **kwargs: notifications.append((args, kwargs))

    result = module.movie_manually_download_specific_subtitle(
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
