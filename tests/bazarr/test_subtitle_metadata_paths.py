import subtitles.wanted.movies as wanted_movies
import subtitles.wanted.series as wanted_series


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
