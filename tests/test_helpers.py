import importlib.util
import sys
from pathlib import Path
from types import ModuleType


class _Condition:
    def __and__(self, other):
        return self


class _Column:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return _Condition()

    def __ne__(self, other):
        return _Condition()

    def in_(self, other):
        return _Condition()

    def is_(self, other):
        return _Condition()

    def is_not(self, other):
        return _Condition()

    def label(self, name):
        return self

    def lower(self):
        return self


class _Query:
    def where(self, *args):
        return self

    def join(self, *args):
        return self

    def select_from(self, *args):
        return self

    def exists(self):
        return self

    def limit(self, *args):
        return self

    def label(self, *args):
        return self

    def with_only_columns(self, *args):
        return self

    def order_by(self, *args):
        return self

    def values(self, *args, **kwargs):
        return self

    def update(self):
        return self


class _Result:
    def __init__(self, first_value=None, all_value=None, scalar_value=None):
        self._first_value = first_value
        self._all_value = [] if all_value is None else all_value
        self._scalar_value = scalar_value

    def first(self):
        return self._first_value

    def all(self):
        return self._all_value

    def scalar(self):
        return self._scalar_value if self._scalar_value is not None else self._first_value

    def __iter__(self):
        return iter(self._all_value)


def _validate_module_name(name, *, allow_dots):
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"Invalid module name: {name}")

    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
    if allow_dots:
        allowed += "."

    if any(character not in allowed for character in name):
        raise ValueError(f"Invalid module name: {name}")

    if allow_dots and (name.startswith(".") or name.endswith(".") or ".." in name):
        raise ValueError(f"Invalid module name: {name}")


def load_isolated_module(module_name, module_path, package_names, module_overrides):
    repo_root = Path(__file__).resolve().parents[1]
    module_path = Path(module_path).resolve()
    module_path.relative_to(repo_root)
    module_names = list(module_overrides)
    original_module_keys = set(sys.modules)
    saved = {
        saved_name: sys.modules.get(saved_name)
        for saved_name in [module_name, *package_names, *module_names]
    }
    try:
        for package_name in package_names:
            package = ModuleType(package_name)
            package.__path__ = []
            sys.modules[package_name] = package

        sys.modules.update(module_overrides)

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load import spec for {module_name} from {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for loaded_name in list(sys.modules):
            if loaded_name in original_module_keys:
                continue

            if any(
                loaded_name == package_name or loaded_name.startswith(f"{package_name}.")
                for package_name in package_names
            ):
                sys.modules.pop(loaded_name, None)

        for saved_name, previous in saved.items():
            if previous is None:
                sys.modules.pop(saved_name, None)
            else:
                sys.modules[saved_name] = previous


def load_wanted_module(name):
    from types import SimpleNamespace

    _validate_module_name(name, allow_dots=False)
    root = Path(__file__).resolve().parents[1]
    module_path = root / "bazarr" / "subtitles" / "wanted" / f"{name}.py"

    package_names = [
        "app",
        "utilities",
        "subtitles",
        "subtitles.indexer",
        "subtitles.wanted",
        "radarr",
        "sonarr",
        "sqlalchemy",
    ]
    path_mappings = SimpleNamespace(
        path_replace_movie=lambda path: path,
        path_replace=lambda path: path,
    )
    table_movies = SimpleNamespace(
        path=_Column("path"),
        missing_subtitles=_Column("missing_subtitles"),
        radarrId=_Column("radarrId"),
        audio_language=_Column("audio_language"),
        sceneName=_Column("sceneName"),
        failedAttempts=_Column("failedAttempts"),
        title=_Column("title"),
        profileId=_Column("profileId"),
        tags=_Column("tags"),
        monitored=_Column("monitored"),
        __table__=SimpleNamespace(
            c=SimpleNamespace(radarrId=_Column("radarrId")),
            update=lambda: _Query(),
        ),
    )
    table_episodes = SimpleNamespace(
        path=_Column("path"),
        missing_subtitles=_Column("missing_subtitles"),
        sonarrEpisodeId=_Column("sonarrEpisodeId"),
        sonarrSeriesId=_Column("sonarrSeriesId"),
        audio_language=_Column("audio_language"),
        sceneName=_Column("sceneName"),
        failedAttempts=_Column("failedAttempts"),
        title=_Column("title"),
        season=_Column("season"),
        episode=_Column("episode"),
        monitored=_Column("monitored"),
        __table__=SimpleNamespace(
            c=SimpleNamespace(sonarrEpisodeId=_Column("sonarrEpisodeId")),
            update=lambda: _Query(),
        ),
    )
    table_shows = SimpleNamespace(
        title=_Column("title"),
        tags=_Column("tags"),
        profileId=_Column("profileId"),
        seriesType=_Column("seriesType"),
        sonarrSeriesId=_Column("sonarrSeriesId"),
    )
    table_missing = SimpleNamespace(
        id=_Column("id"),
        media_type=_Column("media_type"),
        media_id=_Column("media_id"),
        language=_Column("language"),
    )
    database_module = SimpleNamespace(
        get_exclusion_clause=lambda media_type: [],
        get_audio_profile_languages=lambda audio_language: [{"name": "English"}],
        TableMovies=table_movies,
        TableShows=table_shows,
        TableEpisodes=table_episodes,
        TableMissingSubtitles=table_missing,
        TableMoviesSubtitles=SimpleNamespace(
            id=_Column("id"),
            radarrId=_Column("radarrId"),
            path=_Column("path"),
            embedded_track_id=_Column("embedded_track_id"),
        ),
        TableEpisodesSubtitles=SimpleNamespace(
            id=_Column("id"),
            sonarrEpisodeId=_Column("sonarrEpisodeId"),
            path=_Column("path"),
            embedded_track_id=_Column("embedded_track_id"),
        ),
        database=SimpleNamespace(),
        update=lambda *args, **kwargs: _Query(),
        select=lambda *args, **kwargs: _Query(),
        get_subtitles=lambda **kwargs: [],
    )
    module_overrides = {
        "utilities.path_mappings": SimpleNamespace(path_mappings=path_mappings),
        "subtitles.indexer.movies": SimpleNamespace(
            store_subtitles_movie=lambda *args, **kwargs: None,
            list_missing_subtitles_movies=lambda *args, **kwargs: None,
        ),
        "subtitles.indexer.series": SimpleNamespace(
            store_subtitles=lambda *args, **kwargs: None,
            list_missing_subtitles=lambda *args, **kwargs: None,
        ),
        "radarr.history": SimpleNamespace(history_log_movie=lambda *args, **kwargs: None),
        "sonarr.history": SimpleNamespace(history_log=lambda *args, **kwargs: None),
        "app.notifier": SimpleNamespace(
            send_notifications_movie=lambda *args, **kwargs: None,
            send_notifications=lambda *args, **kwargs: None,
        ),
        "app.get_providers": SimpleNamespace(get_providers=lambda: ["provider"]),
        "app.database": database_module,
        "app.event_handler": SimpleNamespace(event_stream=lambda **kwargs: None),
        "app.jobs_queue": SimpleNamespace(
            jobs_queue=SimpleNamespace(
                add_job_from_function=lambda *args, **kwargs: None,
                update_job_progress=lambda *args, **kwargs: None,
                update_job_name=lambda *args, **kwargs: None,
            )
        ),
        "app.config": SimpleNamespace(
            settings=SimpleNamespace(
                general=SimpleNamespace(
                    use_whisper_fallback=True,
                    use_whisper_fallback_series=True,
                )
            )
        ),
        "subtitles.adaptive_searching": SimpleNamespace(
            is_search_active=lambda desired_language, attempt_string: False,
            updateFailedAttempts=lambda desired_language, attempt_string: f"updated:{desired_language}",
            get_adaptive_search_policy=lambda: {"policy": "adaptive"},
        ),
        "subtitles.download": SimpleNamespace(generate_subtitles=lambda *args, **kwargs: iter(())),
        "subtitles.wanted_state": SimpleNamespace(
            due_missing_languages_statement=lambda *args, **kwargs: _Query(),
            get_due_missing_languages_map=lambda *args, **kwargs: {},
            get_due_missing_languages_for_media=lambda *args, **kwargs: [],
            get_missing_languages=lambda *args, **kwargs: [],
            iter_due_missing_languages_maps=lambda *args, **kwargs: iter(()),
            record_failed_subtitle_attempts=lambda *args, **kwargs: "updated",
            record_failed_subtitle_attempts_map=lambda *args, **kwargs: {},
        ),
        "subtitles.wanted.utils": SimpleNamespace(
            get_language_search_items=lambda languages: [
                (
                    language.split(":")[0],
                    "True" if language.endswith(":hi") else "False",
                    "True" if language.endswith(":forced") else "False",
                )
                for language in languages
            ]
        ),
        "sqlalchemy": SimpleNamespace(
            bindparam=lambda name: name,
            case=lambda *args, **kwargs: None,
            func=SimpleNamespace(count=lambda value: value, distinct=lambda value: value),
        ),
    }
    return load_isolated_module(
        f"subtitles.wanted.{name}",
        module_path,
        package_names,
        module_overrides,
    )


def load_sync_module(name):
    from types import SimpleNamespace

    _validate_module_name(name, allow_dots=True)
    root = Path(__file__).resolve().parents[1]
    relative = Path(*name.split(".")).with_suffix(".py")
    module_path = root / "bazarr" / relative

    package_names = [
        "app",
        "utilities",
        "subtitles",
        "subtitles.indexer",
        "radarr",
        "radarr.sync",
        "sonarr",
        "sonarr.sync",
        "sqlalchemy",
        "sqlalchemy.exc",
    ]
    table_movies = SimpleNamespace(
        radarrId=_Column("radarrId"),
        missing_subtitles=_Column("missing_subtitles"),
        failedAttempts=_Column("failedAttempts"),
        movie_file_id=_Column("movie_file_id"),
        path=_Column("path"),
        monitored=_Column("monitored"),
        __table__=SimpleNamespace(columns=[_Column("radarrId"), _Column("path")], c=SimpleNamespace(radarrId=_Column("radarrId"))),
    )
    table_shows = SimpleNamespace(
        sonarrSeriesId=_Column("sonarrSeriesId"),
        title=_Column("title"),
        year=_Column("year"),
        path=_Column("path"),
        profileId=_Column("profileId"),
        seriesType=_Column("seriesType"),
    )
    table_episodes = SimpleNamespace(
        sonarrEpisodeId=_Column("sonarrEpisodeId"),
        sonarrSeriesId=_Column("sonarrSeriesId"),
        missing_subtitles=_Column("missing_subtitles"),
        failedAttempts=_Column("failedAttempts"),
        episode_file_id=_Column("episode_file_id"),
        path=_Column("path"),
        monitored=_Column("monitored"),
        season=_Column("season"),
        episode=_Column("episode"),
        title=_Column("title"),
        __table__=SimpleNamespace(columns=[_Column("sonarrEpisodeId"), _Column("path")], c=SimpleNamespace(sonarrEpisodeId=_Column("sonarrEpisodeId"))),
    )
    database_module = SimpleNamespace(
        TableMovies=table_movies,
        TableShows=table_shows,
        TableEpisodes=table_episodes,
        TableLanguagesProfiles=SimpleNamespace(profileId=_Column("profileId"), name=_Column("name"), tag=_Column("tag")),
        TableMissingSubtitles=SimpleNamespace(
            media_type=_Column("media_type"),
            media_id=_Column("media_id"),
            language=_Column("language"),
        ),
        database=SimpleNamespace(),
        insert=lambda *args, **kwargs: _Query(),
        update=lambda *args, **kwargs: _Query(),
        delete=lambda *args, **kwargs: _Query(),
        select=lambda *args, **kwargs: _Query(),
        get_exclusion_clause=lambda media_type: [],
    )
    module_overrides = {
        "app.config": SimpleNamespace(
            settings=SimpleNamespace(
                general=SimpleNamespace(
                    debug=False,
                    enable_strm_support=False,
                    movie_default_enabled=False,
                    parse_embedded_audio_track=False,
                    notify_if_nothing_is_missing_for_signalr_event=False,
                ),
                radarr=SimpleNamespace(apikey="radarr-key", sync_only_monitored_movies=False),
                sonarr=SimpleNamespace(
                    apikey="sonarr-key",
                    sync_only_monitored_series=False,
                    sync_only_monitored_episodes=False,
                ),
            )
        ),
        "app.database": database_module,
        "app.event_handler": SimpleNamespace(event_stream=lambda **kwargs: None),
        "app.jobs_queue": SimpleNamespace(
            jobs_queue=SimpleNamespace(
                add_job_from_function=lambda *args, **kwargs: None,
                update_job_progress=lambda *args, **kwargs: None,
                update_job_name=lambda *args, **kwargs: None,
                feed_jobs_pending_queue=lambda *args, **kwargs: None,
            )
        ),
        "app.notifier": SimpleNamespace(
            send_notifications_movie=lambda *args, **kwargs: None,
            send_notifications=lambda *args, **kwargs: None,
        ),
        "utilities.path_mappings": SimpleNamespace(
            path_mappings=SimpleNamespace(
                path_replace_movie=lambda path: path,
                path_replace=lambda path: path,
            )
        ),
        "constants": SimpleNamespace(MINIMUM_VIDEO_SIZE=100),
        "subtitles.indexer.movies": SimpleNamespace(store_subtitles_movie=lambda *args, **kwargs: None),
        "subtitles.indexer.series": SimpleNamespace(
            store_subtitles=lambda *args, **kwargs: None,
            series_full_scan_subtitles=lambda *args, **kwargs: None,
            list_missing_subtitles=lambda *args, **kwargs: None,
        ),
        "subtitles.mass_download": SimpleNamespace(
            movies_download_subtitles=lambda *args, **kwargs: None,
            episode_download_subtitles=lambda *args, **kwargs: None,
        ),
        "subtitles.adaptive_searching": SimpleNamespace(
            is_search_active=lambda *args, **kwargs: False,
        ),
        "subtitles.wanted_state": SimpleNamespace(
            delete_wanted_search_state=lambda *args, **kwargs: None,
            get_due_missing_languages_for_media=lambda *args, **kwargs: [],
            get_due_missing_languages_map=lambda *args, **kwargs: {},
        ),
        "radarr.rootfolder": SimpleNamespace(check_radarr_rootfolder=lambda: None),
        "radarr.sync.parser": SimpleNamespace(movieParser=lambda *args, **kwargs: {}),
        "radarr.sync.utils": SimpleNamespace(
            get_profile_list=lambda: [],
            get_tags=lambda: {},
            get_movies_from_radarr_api=lambda **kwargs: [],
        ),
        "sonarr.info": SimpleNamespace(
            get_sonarr_info=SimpleNamespace(
                is_legacy=lambda: False,
                semver=lambda: (4, 0, 9, 2421),
            )
        ),
        "sonarr.sync.parser": SimpleNamespace(episodeParser=lambda *args, **kwargs: {}),
        "sonarr.sync.utils": SimpleNamespace(
            get_episodes_from_sonarr_api=lambda **kwargs: [],
            get_episodesFiles_from_sonarr_api=lambda **kwargs: [],
        ),
        "semver": SimpleNamespace(Version=lambda *args: tuple(args)),
        "sqlalchemy.exc": SimpleNamespace(IntegrityError=RuntimeError),
    }
    return load_isolated_module(name, module_path, package_names, module_overrides)


def load_mass_download_module(name):
    from types import SimpleNamespace

    _validate_module_name(name, allow_dots=False)
    root = Path(__file__).resolve().parents[1]
    module_path = root / "bazarr" / "subtitles" / "mass_download" / f"{name}.py"

    package_names = [
        "subtitles",
        "subtitles.mass_download",
        "subtitles.indexer",
        "utilities",
        "app",
        "radarr",
        "sonarr",
    ]
    path_mappings = SimpleNamespace(
        path_replace_movie=lambda path: path,
        path_replace=lambda path: path,
    )
    database_module = SimpleNamespace(
        get_exclusion_clause=lambda media_type: [],
        get_audio_profile_languages=lambda audio_language: [{"name": "English"}],
        TableMovies=SimpleNamespace(
            path=_Column("path"),
            missing_subtitles=_Column("missing_subtitles"),
            audio_language=_Column("audio_language"),
            radarrId=_Column("radarrId"),
            sceneName=_Column("sceneName"),
            title=_Column("title"),
            year=_Column("year"),
            tags=_Column("tags"),
            monitored=_Column("monitored"),
            profileId=_Column("profileId"),
        ),
        TableShows=SimpleNamespace(
            path=_Column("path"),
            title=_Column("title"),
            sonarrSeriesId=_Column("sonarrSeriesId"),
            tags=_Column("tags"),
            profileId=_Column("profileId"),
            seriesType=_Column("seriesType"),
        ),
        TableEpisodes=SimpleNamespace(
            path=_Column("path"),
            missing_subtitles=_Column("missing_subtitles"),
            monitored=_Column("monitored"),
            sonarrEpisodeId=_Column("sonarrEpisodeId"),
            sceneName=_Column("sceneName"),
            title=_Column("title"),
            season=_Column("season"),
            episode=_Column("episode"),
            audio_language=_Column("audio_language"),
            sonarrSeriesId=_Column("sonarrSeriesId"),
        ),
        TableMissingSubtitles=SimpleNamespace(
            id=_Column("id"),
            media_type=_Column("media_type"),
            media_id=_Column("media_id"),
        ),
        database=SimpleNamespace(),
        select=lambda *args: _Query(),
        get_profile_id=lambda **kwargs: 101,
        get_subtitles=lambda **kwargs: [],
    )
    indexer_module = SimpleNamespace(
        store_subtitles_movie=lambda *args, **kwargs: None,
        list_missing_subtitles_movies=lambda *args, **kwargs: None,
        store_subtitles=lambda *args, **kwargs: None,
        list_missing_subtitles=lambda *args, **kwargs: None,
    )
    notifier_module = SimpleNamespace(
        send_notifications_movie=lambda *args, **kwargs: None,
        send_notifications=lambda *args, **kwargs: None,
    )
    module_overrides = {
        "utilities.path_mappings": SimpleNamespace(path_mappings=path_mappings),
        "app.database": database_module,
        "app.get_providers": SimpleNamespace(get_providers=lambda: ["provider"]),
        "app.jobs_queue": SimpleNamespace(
            jobs_queue=SimpleNamespace(
                add_job_from_function=lambda *args, **kwargs: None,
                update_job_progress=lambda *args, **kwargs: None,
                update_job_name=lambda *args, **kwargs: None,
            )
        ),
        "app.event_handler": SimpleNamespace(event_stream=lambda **kwargs: None),
        "app.notifier": notifier_module,
        "app.config": SimpleNamespace(
            settings=SimpleNamespace(
                general=SimpleNamespace(
                    use_whisper_fallback=True,
                    use_whisper_fallback_series=True,
                )
            )
        ),
        "subtitles.download": SimpleNamespace(generate_subtitles=lambda *args, **kwargs: iter(())),
        "subtitles.serialization": SimpleNamespace(
            missing_subtitle_to_language_tuple=lambda language: (
                language.split(":")[0],
                "True" if language.endswith(":hi") else "False",
                "True" if language.endswith(":forced") else "False",
            )
        ),
        "subtitles.wanted_state": SimpleNamespace(
            get_missing_languages=lambda media_type, media_id: [],
            get_missing_languages_map=lambda media_type, media_ids: {},
        ),
        f"subtitles.indexer.{name}": indexer_module,
        "radarr.history": SimpleNamespace(history_log_movie=lambda *args, **kwargs: None),
        "sonarr.history": SimpleNamespace(history_log=lambda *args, **kwargs: None),
    }
    return load_isolated_module(
        f"subtitles.mass_download.{name}",
        module_path,
        package_names,
        module_overrides,
    )


# ===== Test Utility Functions =====

def parse_language_code(lang_str: str) -> tuple:
    """Parse language string into (code, hi_flag, forced_flag) tuple.
    
    Examples:
        'en' -> ('en', 'False', 'False')
        'en:hi' -> ('en', 'True', 'False')
        'fr:forced' -> ('fr', 'False', 'True')
    """
    base = lang_str.split(":")[0]
    hi = "True" if lang_str.endswith(":hi") else "False"
    forced = "True" if lang_str.endswith(":forced") else "False"
    return (base, hi, forced)


def unwrap_result_tuple(result):
    """Unwrap single-element tuple if needed."""
    if isinstance(result, tuple) and len(result):
        return result[0]
    return result


def get_current_timestamp() -> int:
    """Get current Unix timestamp."""
    from datetime import datetime
    return int(datetime.timestamp(datetime.now()))


def set_adaptive_search_settings(monkeypatch, *, enabled: bool = True, 
                                 delay: str = "3w", delta: str = "1w"):
    """Configure adaptive search settings via monkeypatch."""
    monkeypatch.setattr('bazarr.subtitles.adaptive_searching.settings.general.adaptive_searching', enabled)
    if enabled:
        monkeypatch.setattr('bazarr.subtitles.adaptive_searching.settings.general.adaptive_searching_delay', delay)
        monkeypatch.setattr('bazarr.subtitles.adaptive_searching.settings.general.adaptive_searching_delta', delta)
