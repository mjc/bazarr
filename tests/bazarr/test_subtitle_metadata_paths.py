from types import SimpleNamespace

from sqlalchemy import select

import subtitles.download as download
import subtitles.indexer.movies as movie_indexer
import subtitles.indexer.series as series_indexer
import subtitles.pool as subtitle_pool
import subtitles.upgrade as upgrade
import subtitles.wanted.movies as wanted_movies
import subtitles.wanted.series as wanted_series


def _profile(items):
    return {"items": items, "originalFormat": False}


def _unexpected_call(*unused_args, **unused_kwargs):
    raise AssertionError("get_video should not be called without a subtitle profile")


def test_series_missing_subtitles_matches_normalized_profile_flags(
    bind_wanted_database,
    episode_row_factory,
    episode_subtitle_row_factory,
    transactional_session,
    wanted_search_tables,
    monkeypatch,
):
    module = bind_wanted_database(series_indexer, "series")
    episode_row_factory(sonarrEpisodeId=17, profileId=22)
    episode_subtitle_row_factory(sonarrEpisodeId=17, language="en", forced=False, hi=True)

    monkeypatch.setattr(module.settings.general, "use_embedded_subs", True)
    monkeypatch.setattr(module, "get_profile_cutoff", lambda profile_id: [])
    monkeypatch.setattr(module, "event_stream", lambda **kwargs: None)
    monkeypatch.setattr(
        module,
        "get_profiles_list",
        lambda profile_id: _profile(
            [
                {
                    "language": "en",
                    "forced": False,
                    "hi": False,
                    "audio_exclude": False,
                    "audio_only_include": False,
                },
                {
                    "language": "fr",
                    "forced": True,
                    "hi": False,
                    "audio_exclude": False,
                    "audio_only_include": False,
                },
            ]
        ),
    )

    module.list_missing_subtitles(epno=17)

    missing = transactional_session.execute(
        select(wanted_search_tables.episode.c.missing_subtitles).where(
            wanted_search_tables.episode.c.sonarrEpisodeId == 17
        )
    ).scalar_one()
    assert missing == "['fr:forced']"


def test_movie_missing_subtitles_matches_normalized_profile_flags(
    bind_wanted_database,
    movie_row_factory,
    movie_subtitle_row_factory,
    transactional_session,
    wanted_search_tables,
    monkeypatch,
):
    module = bind_wanted_database(movie_indexer, "movies")
    movie_row_factory(radarrId=7, profileId=11)
    movie_subtitle_row_factory(radarrId=7, language="en", forced=False, hi=True)

    monkeypatch.setattr(module.settings.general, "use_embedded_subs", True)
    monkeypatch.setattr(module, "get_profile_cutoff", lambda profile_id: [])
    monkeypatch.setattr(module, "event_stream", lambda **kwargs: None)
    monkeypatch.setattr(
        module,
        "get_profiles_list",
        lambda profile_id: _profile(
            [
                {
                    "language": "en",
                    "forced": False,
                    "hi": False,
                    "audio_exclude": False,
                    "audio_only_include": False,
                },
                {
                    "language": "fr",
                    "forced": False,
                    "hi": True,
                    "audio_exclude": False,
                    "audio_only_include": False,
                },
            ]
        ),
    )

    module.list_missing_subtitles_movies(no=7)

    missing = transactional_session.execute(
        select(wanted_search_tables.movie.c.missing_subtitles).where(
            wanted_search_tables.movie.c.radarrId == 7
        )
    ).scalar_one()
    assert missing == "['fr:hi']"


def test_wanted_episode_passes_none_scene_name_to_download(
    bind_wanted_database,
    episode_row_factory,
    monkeypatch,
):
    module = bind_wanted_database(wanted_series, "series")
    episode = episode_row_factory(sonarrEpisodeId=17, sceneName=None, missing_subtitles="['en']")
    scenes = []

    monkeypatch.setattr(module.path_mappings, "path_replace", lambda path: path)
    monkeypatch.setattr(module, "is_search_active", lambda **kwargs: True)
    monkeypatch.setattr(module, "updateFailedAttempts", lambda **kwargs: "[]")
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: scenes.append(args[3]) or [])

    module._wanted_episode(episode, providers_list={"provider"})

    assert scenes == [None]


def test_wanted_movie_passes_none_scene_name_to_download(
    bind_wanted_database,
    movie_row_factory,
    monkeypatch,
):
    module = bind_wanted_database(wanted_movies, "movies")
    movie = movie_row_factory(radarrId=7, sceneName=None, missing_subtitles="['en']")
    scenes = []

    monkeypatch.setattr(module.path_mappings, "path_replace_movie", lambda path: path)
    monkeypatch.setattr(module, "is_search_active", lambda **kwargs: True)
    monkeypatch.setattr(module, "updateFailedAttempts", lambda **kwargs: "[]")
    monkeypatch.setattr(module, "generate_subtitles", lambda *args, **kwargs: scenes.append(args[3]) or [])

    module._wanted_movie(movie, providers_list={"provider"})

    assert scenes == [None]


def test_generate_subtitles_returns_none_when_profile_is_missing(monkeypatch):
    language = SimpleNamespace(hi=False, forced=False)

    monkeypatch.setattr(subtitle_pool, "_update_pool", lambda media_type, profile_id: False)
    monkeypatch.setattr(download, "_get_pool", lambda media_type, profile_id: SimpleNamespace(providers={"provider"}))
    monkeypatch.setattr(download, "_get_language_obj", lambda languages: [language])
    monkeypatch.setattr(download, "get_profiles_list", lambda profile_id: None)
    monkeypatch.setattr(download, "get_video", _unexpected_call)

    result = list(
        download.generate_subtitles(
            "/movies/movie.mkv",
            [("en", "False", "False")],
            "English",
            None,
            "Movie",
            "movie",
            44,
        )
    )

    assert result == []


def test_upgrade_language_from_items_uses_boolean_profile_flags():
    items = [
        {"language": "en", "forced": True, "hi": False},
        {"language": "fr", "forced": False, "hi": True},
        {"language": "de", "forced": False, "hi": False},
    ]

    assert upgrade._language_from_items(items) == ["en:forced", "fr:hi", "de", "de:hi"]


def test_upgrade_hi_required_uses_boolean_profile_flags(monkeypatch):
    monkeypatch.setattr(
        upgrade,
        "get_profiles_list",
        lambda profile_id: _profile(
            [
                {"language": "en", "forced": False, "hi": False},
                {"language": "fr", "forced": False, "hi": True},
            ]
        ),
    )

    assert upgrade._is_hi_required("fr", profile_id=22) is True
    assert upgrade._is_hi_required("en", profile_id=22) is False
