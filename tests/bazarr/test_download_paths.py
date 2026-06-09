import random
from pathlib import Path
from types import SimpleNamespace

from tests.test_helpers import load_isolated_module


class _Condition:
    def __and__(self, other):
        return self


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return _Condition()


class _Query:
    def where(self, *args, **kwargs):
        return self


def _load_download_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "bazarr" / "subtitles" / "download.py"

    class _Language:
        def __init__(self, code, hi=False, forced=False):
            self.basename = code
            self.hi = hi
            self.forced = forced

        @staticmethod
        def rebuild(lang, hi=False, forced=False):
            return _Language(lang.basename, hi=hi or lang.hi, forced=forced or lang.forced)

    table_episodes = SimpleNamespace(
        missing_subtitles=_Column("missing_subtitles"),
        path=_Column("path"),
    )
    table_movies = SimpleNamespace(
        missing_subtitles=_Column("missing_subtitles"),
        path=_Column("path"),
    )

    return load_isolated_module(
        "subtitles.download",
        module_path,
        ["app", "utilities", "languages", "subtitles", "subzero", "subliminal_patch"],
        {
            "app.config": SimpleNamespace(
                settings=SimpleNamespace(general=SimpleNamespace()),
                get_array_from=lambda value: value,
            ),
            "app.database": SimpleNamespace(
                TableEpisodes=table_episodes,
                TableMovies=table_movies,
                database=SimpleNamespace(execute=lambda stmt: None),
                select=lambda *args, **kwargs: _Query(),
                get_profiles_list=lambda profile_id: {"originalFormat": 0},
            ),
            "utilities.path_mappings": SimpleNamespace(
                path_mappings=SimpleNamespace(
                    path_replace_reverse=lambda path: path,
                    path_replace_reverse_movie=lambda path: path,
                )
            ),
            "utilities.helper": SimpleNamespace(
                get_target_folder=lambda path: "",
                force_unicode=lambda path: path,
            ),
            "languages.get_languages": SimpleNamespace(alpha3_from_alpha2=lambda code: code),
            "subtitles.pool": SimpleNamespace(
                update_pools=lambda fn: fn,
                _get_pool=lambda media_type, profile_id: SimpleNamespace(
                    providers=["provider"],
                    provider_configs={},
                ),
            ),
            "subtitles.utils": SimpleNamespace(
                get_video=lambda *args, **kwargs: None,
                _get_lang_obj=lambda lang: _Language(lang),
                _get_scores=lambda *args, **kwargs: (0, 100, []),
                _set_forced_providers=lambda *args, **kwargs: None,
            ),
            "subtitles.processing": SimpleNamespace(process_subtitle=lambda **kwargs: None),
            "subzero.language": SimpleNamespace(Language=_Language),
            "subliminal_patch.core": SimpleNamespace(save_subtitles=lambda *args, **kwargs: []),
            "subliminal_patch.core_persistent": SimpleNamespace(
                download_best_subtitles=lambda *args, **kwargs: {}
            ),
            "subliminal": SimpleNamespace(
                region=SimpleNamespace(backend=SimpleNamespace(sync=lambda: None))
            ),
        },
    )


def test_check_missing_languages_fuzz_malformed_movie_values_fail_safe():
    module = _load_download_module()
    rng = random.Random(5318)
    malformed_values = [
        "",
        " ",
        "[",
        "not_a_list",
        "None",
        "{'en': 1}",
        "[1, 2, 3]",
        "[None, 1, {'x': 1}]",
    ]
    malformed_values.extend(
        "".join(rng.choice("[]{}()'\",abc123:-_ ") for _ in range(rng.randint(1, 20)))
        for _ in range(120)
    )

    for malformed in malformed_values:
        module.database = SimpleNamespace(
            execute=lambda stmt, _malformed=malformed: SimpleNamespace(
                first=lambda: SimpleNamespace(missing_subtitles=_malformed)
            )
        )
        assert module.check_missing_languages("/movies/movie.mkv", "movie") == []


def test_check_missing_languages_fuzz_malformed_series_values_fail_safe():
    module = _load_download_module()
    rng = random.Random(8153)
    malformed_values = [
        "",
        " ",
        "[",
        "not_a_list",
        "None",
        "{'en': 1}",
        "[1, 2, 3]",
        "[None, 1, {'x': 1}]",
    ]
    malformed_values.extend(
        "".join(rng.choice("[]{}()'\",abc123:-_ ") for _ in range(rng.randint(1, 20)))
        for _ in range(120)
    )

    for malformed in malformed_values:
        module.database = SimpleNamespace(
            execute=lambda stmt, _malformed=malformed: SimpleNamespace(
                first=lambda: SimpleNamespace(missing_subtitles=_malformed)
            )
        )
        assert module.check_missing_languages("/series/e01.mkv", "series") == []


def test_check_missing_languages_filters_non_string_entries():
    module = _load_download_module()
    module.database = SimpleNamespace(
        execute=lambda stmt: SimpleNamespace(
            first=lambda: SimpleNamespace(missing_subtitles="['en:hi', None, 1, {'x': 1}, 'fr:forced']")
        )
    )
    module._get_language_obj = lambda languages: languages

    parsed = module.check_missing_languages("/movies/movie.mkv", "movie")

    assert parsed == [("en", "True", "False"), ("fr", "False", "True")]
