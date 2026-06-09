from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from tests.test_helpers import _Column, _Query, _Result, load_isolated_module


def _load_upload_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "bazarr" / "subtitles" / "upload.py"

    class _Language:
        def __init__(self, code):
            self.code = code

        @staticmethod
        def rebuild(lang, hi=False, forced=False):
            return lang

    class _Subtitle:
        def __init__(self, *args, **kwargs):
            self.content = b""
            self.mods = None
            self.format = ("srt",)

        def is_valid(self):
            return True

        def set_encoding(self, *_args, **_kwargs):
            return None

    table_episodes = SimpleNamespace(
        sonarrSeriesId=_Column("sonarrSeriesId"),
        sonarrEpisodeId=_Column("sonarrEpisodeId"),
        season=_Column("season"),
        episode=_Column("episode"),
        profileId=_Column("profileId"),
        path=_Column("path"),
        title=_Column("title"),
    )
    table_movies = SimpleNamespace(
        radarrId=_Column("radarrId"),
        profileId=_Column("profileId"),
        imdbId=_Column("imdbId"),
        tmdbId=_Column("tmdbId"),
    )
    table_shows = SimpleNamespace(
        profileId=_Column("profileId"),
        imdbId=_Column("imdbId"),
        tvdbId=_Column("tvdbId"),
    )

    return load_isolated_module(
        "subtitles.upload",
        module_path,
        ["subtitles", "app", "utilities", "radarr", "sonarr", "languages", "plex", "jellyfin"],
        {
            "subzero.language": SimpleNamespace(Language=_Language),
            "subliminal_patch.core": SimpleNamespace(save_subtitles=lambda *args, **kwargs: [SimpleNamespace(storage_path="/tmp/sub.srt")]),
            "subliminal_patch.subtitle": SimpleNamespace(Subtitle=_Subtitle),
            "subliminal_patch.score": SimpleNamespace(MAX_SCORES={"episode": 360, "movie": 120}),
            "pysubs2.formats": SimpleNamespace(get_format_identifier=lambda ext: "srt"),
            "languages.get_languages": SimpleNamespace(
                language_from_alpha3=lambda code: "English",
                alpha2_from_alpha3=lambda code: "en",
                alpha3_from_alpha2=lambda code: "eng",
            ),
            "languages.custom_lang": SimpleNamespace(CustomLanguage=SimpleNamespace(from_value=lambda *args, **kwargs: None)),
            "app.config": SimpleNamespace(
                settings=SimpleNamespace(
                    general=SimpleNamespace(
                        single_language=False,
                        use_postprocessing=False,
                        postprocessing_cmd="",
                        chmod="0644",
                        chmod_enabled=False,
                        utf8_encode=False,
                        subzero_mods=[],
                        dont_notify_manual_actions=True,
                        use_plex=False,
                        use_jellyfin=False,
                    ),
                    plex=SimpleNamespace(update_series_library=False, set_episode_added=False, update_movie_library=False, set_movie_added=False),
                    jellyfin=SimpleNamespace(update_series_library=False, update_movie_library=False),
                ),
                get_array_from=lambda value: value,
            ),
            "utilities.helper": SimpleNamespace(get_target_folder=lambda path: None, force_unicode=lambda path: path),
            "utilities.post_processing": SimpleNamespace(pp_replace=lambda *args, **kwargs: "", set_chmod=lambda **kwargs: None),
            "utilities.path_mappings": SimpleNamespace(
                path_mappings=SimpleNamespace(
                    path_replace_reverse=lambda path: path,
                    path_replace_reverse_movie=lambda path: path,
                )
            ),
            "radarr.history": SimpleNamespace(history_log_movie=lambda *args, **kwargs: None),
            "radarr.notify": SimpleNamespace(notify_radarr=lambda *args, **kwargs: None),
            "sonarr.history": SimpleNamespace(history_log=lambda *args, **kwargs: None),
            "sonarr.notify": SimpleNamespace(notify_sonarr=lambda *args, **kwargs: None),
            "app.database": SimpleNamespace(
                TableEpisodes=table_episodes,
                TableMovies=table_movies,
                TableShows=table_shows,
                get_profiles_list=lambda profile_id: {},
                get_audio_profile_languages=lambda audio_language: None,
                database=SimpleNamespace(execute=lambda stmt: _Result(first_value=None)),
                select=lambda *args, **kwargs: _Query(),
            ),
            "app.jobs_queue": SimpleNamespace(jobs_queue=SimpleNamespace(add_job_from_function=lambda *args, **kwargs: "job")),
            "app.event_handler": SimpleNamespace(event_stream=lambda **kwargs: None),
            "app.notifier": SimpleNamespace(send_notifications=lambda *args, **kwargs: None, send_notifications_movie=lambda *args, **kwargs: None),
            "subtitles.indexer.series": SimpleNamespace(store_subtitles=lambda *args, **kwargs: None),
            "subtitles.indexer.movies": SimpleNamespace(store_subtitles_movie=lambda *args, **kwargs: None),
            "subtitles.processing": SimpleNamespace(ProcessSubtitlesResult=lambda **kwargs: SimpleNamespace(**kwargs)),
            "subtitles.sync": SimpleNamespace(sync_subtitles=lambda *args, **kwargs: None),
            "subtitles.post_processing": SimpleNamespace(postprocessing=lambda *args, **kwargs: None),
            "plex.operations": SimpleNamespace(
                plex_set_movie_added_date_now=lambda *args, **kwargs: None,
                plex_set_episode_added_date_now=lambda *args, **kwargs: None,
                plex_refresh_item=lambda *args, **kwargs: None,
            ),
            "jellyfin.operations": SimpleNamespace(jellyfin_refresh_item=lambda *args, **kwargs: None),
        },
    )


def test_manual_upload_subtitle_series_handles_missing_profile_key_and_none_audio_language():
    module = _load_upload_module()
    episode_metadata = SimpleNamespace(
        sonarrSeriesId=5,
        sonarrEpisodeId=11,
        season=1,
        episode=2,
        profileId=44,
        imdbId="tt123",
        tvdbId=222,
    )
    module.database = SimpleNamespace(execute=lambda stmt: _Result(first_value=episode_metadata))

    result = module.manual_upload_subtitle(
        path="/series/episode.mkv",
        language="en",
        forced=False,
        hi=False,
        media_type="series",
        subtitle=BytesIO(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n"),
        filename="subtitle.srt",
        audio_language="['eng']",
        job_id="job",
        sonarrSeriesId=5,
        sonarrEpisodeId=11,
    )

    assert result == ("", 204)


def test_manual_upload_subtitle_movie_handles_missing_profile_key_and_none_audio_language():
    module = _load_upload_module()
    movie_metadata = SimpleNamespace(
        radarrId=7,
        profileId=44,
        imdbId="tt456",
        tmdbId=333,
    )
    module.database = SimpleNamespace(execute=lambda stmt: _Result(first_value=movie_metadata))

    result = module.manual_upload_subtitle(
        path="/movies/movie.mkv",
        language="en",
        forced=False,
        hi=False,
        media_type="movie",
        subtitle=BytesIO(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n"),
        filename="subtitle.srt",
        audio_language="['eng']",
        job_id="job",
        radarrId=7,
    )

    assert result == ("", 204)


def test_manual_upload_subtitle_postprocessing_handles_partial_audio_language_dict():
    module = _load_upload_module()
    movie_metadata = SimpleNamespace(
        radarrId=7,
        profileId=44,
        imdbId="tt456",
        tmdbId=333,
    )
    module.database = SimpleNamespace(execute=lambda stmt: _Result(first_value=movie_metadata))
    module.settings.general.use_postprocessing = True
    module.get_audio_profile_languages = lambda audio_language: [{"name": "English"}]
    commands = []
    module.pp_replace = lambda *args, **kwargs: commands.append(args) or "cmd"

    result = module.manual_upload_subtitle(
        path="/movies/movie.mkv",
        language="en",
        forced=False,
        hi=False,
        media_type="movie",
        subtitle=BytesIO(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n"),
        filename="subtitle.srt",
        audio_language="['eng']",
        job_id="job",
        radarrId=7,
    )

    assert result == ("", 204)
    assert len(commands) == 1
