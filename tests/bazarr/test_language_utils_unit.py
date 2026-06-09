from pathlib import Path

from tests.test_helpers import load_isolated_module


def _load_language_utils_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "bazarr" / "subtitles" / "language_utils.py"
    return load_isolated_module(
        "subtitles.language_utils",
        module_path,
        ["subtitles"],
        {},
    )


def test_parse_language_token_canonicalizes_case_and_flag_order():
    module = _load_language_utils_module()

    canonical, language_tuple = module.parse_language_token(" EN : HI : Forced ")

    assert canonical == "en:forced:hi"
    assert language_tuple == ("en", "True", "True")


def test_parse_language_token_rejects_missing_base_language():
    module = _load_language_utils_module()

    assert module.parse_language_token(":hi") is None
    assert module.parse_language_token(None) is None


def test_safe_missing_languages_filters_invalid_and_normalizes_tokens():
    module = _load_language_utils_module()

    value = "[' EN ', ':hi', 'fr:HI', None, 'de:Forced:Hi', 'fr:hi']"
    result = module.safe_missing_languages(value, "unit test")

    assert result == ["en", "fr:hi", "de:forced:hi", "fr:hi"]


def test_resolve_audio_language_uses_first_non_empty_name_with_fallback():
    module = _load_language_utils_module()

    audio_languages = [{"name": "   "}, {"code": "eng"}, {"name": " English "}]
    result = module.resolve_audio_language(audio_languages, fallback="fallback")
    empty_result = module.resolve_audio_language([], fallback="fallback")

    assert result == "English"
    assert empty_result == "fallback"


def test_build_search_payload_deduplicates_and_normalizes():
    module = _load_language_utils_module()

    requests, stamps = module.build_search_payload(
        "[' EN ', 'en', 'fr:HI', 'fr:hi', 'de:Forced:Hi']",
        "unit test",
    )

    assert requests == [
        ("en", "False", "False"),
        ("fr", "True", "False"),
        ("de", "True", "True"),
    ]
    assert stamps == ["en", "fr:hi", "de:forced:hi"]


def test_stamp_failed_attempts_chains_updates():
    module = _load_language_utils_module()
    calls = []
    persisted = []

    def _update_fn(desired_language, attempt_string):
        calls.append((desired_language, attempt_string))
        return f"{attempt_string}|{desired_language}"

    def _persist_fn(updated):
        persisted.append(updated)

    final_attempts = module.stamp_failed_attempts(
        ["en", "fr:forced"],
        "seed",
        _update_fn,
        _persist_fn,
    )

    assert calls == [("en", "seed"), ("fr:forced", "seed|en")]
    assert persisted == ["seed|en", "seed|en|fr:forced"]
    assert final_attempts == "seed|en|fr:forced"
