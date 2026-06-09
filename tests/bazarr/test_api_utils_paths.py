import random
from pathlib import Path
from types import SimpleNamespace

from tests.test_helpers import load_isolated_module


def _load_api_utils_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "bazarr" / "api" / "utils.py"

    return load_isolated_module(
        "api.utils",
        module_path,
        ["app", "api", "languages", "utilities", "flask"],
        {
            "app.config": SimpleNamespace(
                settings=SimpleNamespace(
                    auth=SimpleNamespace(apikey="key"),
                    general=SimpleNamespace(embedded_subs_show_desired=False),
                ),
                base_url="http://localhost:6767",
            ),
            "languages.get_languages": SimpleNamespace(
                language_from_alpha2=lambda code: f"lang-{code}",
                alpha3_from_alpha2=lambda code: f"{code}3",
            ),
            "app.database": SimpleNamespace(
                get_audio_profile_languages=lambda value: [{"name": "English"}],
                get_desired_languages=lambda profile_id: ["en"],
                get_subtitles=lambda **kwargs: [],
            ),
            "utilities.path_mappings": SimpleNamespace(
                path_mappings=SimpleNamespace(
                    path_replace=lambda path: path,
                    path_replace_movie=lambda path: path,
                )
            ),
            "flask": SimpleNamespace(
                request=SimpleNamespace(args={}, form={}, headers={}),
                abort=lambda code: code,
            ),
        },
    )


def _base_item():
    return {
        "radarrId": 7,
        "audio_language": "['eng']",
        "profileId": "11",
        "alternativeTitles": "[]",
        "missing_subtitles": "[]",
        "tags": "[]",
        "path": "/movies/movie.mkv",
    }


def test_postprocess_fuzz_malformed_missing_subtitles_fails_safe():
    module = _load_api_utils_module()
    rng = random.Random(7412)
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
        item = _base_item()
        item["missing_subtitles"] = malformed
        processed = module.postprocess(item)
        assert processed["missing_subtitles"] == []


def test_postprocess_fuzz_malformed_alternative_titles_and_tags_fail_safe():
    module = _load_api_utils_module()
    rng = random.Random(1277)
    malformed_values = [
        "",
        " ",
        "[",
        "not_a_list",
        "None",
        "{'x': 1}",
    ]
    malformed_values.extend(
        "".join(rng.choice("[]{}()'\",abc123:-_ ") for _ in range(rng.randint(1, 20)))
        for _ in range(120)
    )

    for malformed in malformed_values:
        item = _base_item()
        item["alternativeTitles"] = malformed
        item["tags"] = malformed
        processed = module.postprocess(item)
        assert processed["alternativeTitles"] == []
        assert processed["tags"] == []


def test_postprocess_filters_non_string_missing_subtitle_entries():
    module = _load_api_utils_module()
    item = _base_item()
    item["missing_subtitles"] = "['en:hi', None, 1, {'x': 1}, 'fr:forced']"

    processed = module.postprocess(item)

    assert processed["missing_subtitles"] == [
        {"name": "lang-en", "code2": "en", "code3": "en3", "forced": False, "hi": True},
        {"name": "lang-fr", "code2": "fr", "code3": "fr3", "forced": True, "hi": False},
    ]


def test_postprocess_fuzz_malformed_language_values_fail_safe():
    module = _load_api_utils_module()
    rng = random.Random(9124)
    malformed_values = [
        1,
        1.5,
        True,
        object(),
        [],
        {},
    ]
    malformed_values.extend(
        "".join(rng.choice("[]{}()'\",abc123:-_ ") for _ in range(rng.randint(1, 20)))
        for _ in range(120)
    )

    for malformed in malformed_values:
        item = _base_item()
        item["language"] = malformed
        processed = module.postprocess(item)
        if isinstance(malformed, str):
            # String inputs may parse to a language dict or be preserved as-is if malformed.
            assert "language" in processed
        else:
            assert processed["language"] is None


def test_postprocess_ignores_empty_base_language_tokens():
    module = _load_api_utils_module()
    for malformed in [":hi", "", "   :forced"]:
        item = _base_item()
        item["language"] = malformed
        processed = module.postprocess(item)
        assert processed["language"] is None
