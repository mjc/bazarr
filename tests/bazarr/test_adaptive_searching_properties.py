import ast
import random
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tests.test_helpers import load_isolated_module


def _random_language(rng):
    base = rng.choice(["en", "fr", "es", "de", "it", "pt"])
    suffix = rng.choice(["", ":hi", ":forced"])
    return f"{base}{suffix}"


def _load_adaptive_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "bazarr" / "subtitles" / "adaptive_searching.py"
    return load_isolated_module(
        "subtitles.adaptive_searching",
        module_path,
        ["app", "subtitles"],
        {
            "app.config": SimpleNamespace(
                settings=SimpleNamespace(
                    general=SimpleNamespace(
                        adaptive_searching=True,
                        adaptive_searching_delay="3w",
                        adaptive_searching_delta="1w",
                    )
                )
            ),
        },
    )


def test_update_failed_attempts_fuzz_invariants():
    module = _load_adaptive_module()
    rng = random.Random(1337)

    for _ in range(300):
        desired_language = _random_language(rng)
        attempts = [
            [_random_language(rng), rng.randint(1_500_000_000, 1_900_000_000)]
            for _ in range(rng.randint(0, 20))
        ]
        updated = module.updateFailedAttempts(desired_language, str(attempts))
        parsed = ast.literal_eval(updated)

        assert isinstance(parsed, list)
        assert all(isinstance(item, list) and len(item) == 2 for item in parsed)

        # Update always sorts by language key
        langs = [item[0] for item in parsed]
        assert langs == sorted(langs)

        # Extract base language codes for comparison (updateFailedAttempts normalizes them)
        base_desired = desired_language.split(":", 1)[0].strip() if desired_language else ''
        
        # Non-target entries should be compacted to initial+latest (base language codes).
        before_non_target_bases = Counter()
        non_target_grouped = {}
        for item in attempts:
            base_lang = item[0].split(":", 1)[0]
            if base_lang == base_desired:
                continue
            non_target_grouped.setdefault(base_lang, []).append(item[1])

        for lang, timestamps in non_target_grouped.items():
            ordered = sorted(timestamps)
            before_non_target_bases[(lang, ordered[0])] += 1
            if len(ordered) > 1 and ordered[-1] != ordered[0]:
                before_non_target_bases[(lang, ordered[-1])] += 1

        after_non_target = Counter(tuple(item) for item in parsed if item[0] != base_desired)
        assert before_non_target_bases == after_non_target

        # Target entries: check they're stored as base codes
        before_target_bases = [item[0].split(":", 1)[0] for item in attempts if item[0].split(":", 1)[0] == base_desired]
        after_target = [item for item in parsed if item[0] == base_desired]

        # If target existed, keep oldest + new; otherwise add one new.
        assert len(after_target) == (2 if before_target_bases else 1)
        if before_target_bases:
            before_target_timestamps = [
                item[1] for item in attempts 
                if item[0].split(":", 1)[0] == base_desired
            ]
            oldest = min(before_target_timestamps)
            assert any(item[1] == oldest for item in after_target)

        now_ts = datetime.timestamp(datetime.now())
        assert any(abs(item[1] - now_ts) < 2 for item in after_target)


def test_is_search_active_never_raises_for_malformed_attempt_strings():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "3w"
    module.settings.general.adaptive_searching_delta = "1w"

    rng = random.Random(4242)
    malformed_inputs = [
        "",
        " ",
        "[",
        "]",
        "not_a_list",
        "{'oops': 1}",
        "None",
        "[['en']]",
        "[['en', 'not-a-timestamp']]",
    ]
    malformed_inputs.extend("".join(rng.choice("[]{}()'\",abc123") for _ in range(rng.randint(1, 20)))
                            for _ in range(200))

    for bad_input in malformed_inputs:
        result = module.is_search_active("en", bad_input)
        assert result is True


def test_update_failed_attempts_never_raises_for_malformed_attempt_strings():
    module = _load_adaptive_module()
    rng = random.Random(9001)
    malformed_inputs = [
        "",
        " ",
        "[",
        "]",
        "not_a_list",
        "{'oops': 1}",
        "None",
        "[['en']]",
        "[['en', 'not-a-timestamp']]",
    ]
    malformed_inputs.extend("".join(rng.choice("[]{}()'\",abc123") for _ in range(rng.randint(1, 20)))
                            for _ in range(200))

    for bad_input in malformed_inputs:
        updated = module.updateFailedAttempts("en", bad_input)
        parsed = ast.literal_eval(updated)
        assert isinstance(parsed, list)
        assert any(item[0] == "en" for item in parsed)


def test_adaptive_searching_handles_non_string_desired_language():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "3w"
    module.settings.general.adaptive_searching_delta = "1w"

    non_string_values = [None, 0, 1, 3.14, [], {}, object()]
    for value in non_string_values:
        # Fails safe: malformed desired_language should not crash either path.
        assert module.is_search_active(value, "[]") is True
        updated = module.updateFailedAttempts(value, "[]")
        parsed = ast.literal_eval(updated)
        assert isinstance(parsed, list)
        assert all(item[0] != "" for item in parsed)


def test_is_search_active_respects_multi_digit_delay_weeks():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delta = "1w"

    now_ts = datetime.timestamp(datetime.now())
    two_weeks_ago = now_ts - (14 * 24 * 3600)
    attempts = f"[['en', {two_weeks_ago}], ['en', {now_ts}]]"

    # For delays >= 10 weeks, initial attempt is still within delay window.
    # Search should run regardless of latest timestamp.
    for weeks in range(10, 21):
        module.settings.general.adaptive_searching_delay = f"{weeks}w"
        assert module.is_search_active("en", attempts) is True


def test_is_search_active_respects_multi_digit_delta_weeks():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"

    now_ts = datetime.timestamp(datetime.now())
    four_weeks_ago = now_ts - (28 * 24 * 3600)
    two_weeks_ago = now_ts - (14 * 24 * 3600)
    attempts = f"[['en', {four_weeks_ago}], ['en', {two_weeks_ago}]]"

    # Initial attempt is outside 1w delay, so delta controls decision.
    # For delta >= 10 weeks and latest at 2 weeks ago, search should still be blocked.
    for weeks in range(10, 21):
        module.settings.general.adaptive_searching_delta = f"{weeks}w"
        assert module.is_search_active("en", attempts) is False


def test_is_search_active_fails_safe_on_nonnumeric_delay_values():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delta = "1w"

    attempts = "[['en', 1609459200]]"
    for bad_delay in ["aw", "xd", "--w", " w", "d", "w"]:
        module.settings.general.adaptive_searching_delay = bad_delay
        assert module.is_search_active("en", attempts) is True


def test_is_search_active_fails_safe_on_nonnumeric_delta_values():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"

    attempts = "[['en', 1609459200]]"
    for bad_delta in ["aw", "xd", "--w", " w", "d", "w"]:
        module.settings.general.adaptive_searching_delta = bad_delta
        assert module.is_search_active("en", attempts) is True


def test_is_search_active_handles_epoch_zero_timestamp():
    """Timestamp 0.0 represents Unix epoch (1970-01-01); should not crash and should allow search"""
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    # Epoch 0 is 1970-01-01, definitely > delay window
    attempts = "[['en', 0], ['en', 0.0]]"
    result = module.is_search_active("en", attempts)
    assert result is True, "Epoch 0 timestamp should allow search (ancient attempt)"

    updated = module.updateFailedAttempts("en", attempts)
    parsed = ast.literal_eval(updated)
    assert isinstance(parsed, list)
    assert any(item[0] == "en" for item in parsed)


def test_is_search_active_handles_negative_timestamps():
    """Negative timestamps (before 1970) should not crash"""
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    # Negative timestamps (before 1970)
    attempts = "[['en', -1000000], ['en', -1]]"
    result = module.is_search_active("en", attempts)
    assert result is True, "Negative timestamps should allow search or fail safe"

    updated = module.updateFailedAttempts("en", attempts)
    parsed = ast.literal_eval(updated)
    assert isinstance(parsed, list)


def test_is_search_active_handles_very_large_timestamps():
    """Very large timestamps beyond Unix time range should fail safe"""
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    # Timestamps beyond valid Unix range (year 2262 issue)
    attempts = "[['en', 9999999999], ['en', 99999999999999]]"
    result = module.is_search_active("en", attempts)
    assert result is True, "Overflow timestamps should fail safe to True"


def test_is_search_active_handles_multi_colon_language_codes():
    """Language codes with multiple colons should not crash"""
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "3w"
    module.settings.general.adaptive_searching_delta = "1w"

    now_ts = datetime.timestamp(datetime.now())
    
    # Multi-colon language codes
    attempts = f"[['en:hi:forced', {now_ts}], ['en:hi', {now_ts}]]"
    result = module.is_search_active("en:hi:forced", attempts)
    assert result is True, "Multi-colon languages should be handled"

    # Also test with colon-only desired language
    result = module.is_search_active(":", attempts)
    assert result is True, "Colon-only language should fail safe"


def test_is_search_active_handles_only_flag_desired_language():
    """Desired language that is only a flag (no base language) should fail safe"""
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    now_ts = datetime.timestamp(datetime.now())
    attempts = f"[['en', {now_ts}], ['fr:hi', {now_ts}]]"
    
    # Just flag, no base language
    for flag_only in [":hi", ":forced", "::hi"]:
        result = module.is_search_active(flag_only, attempts)
        assert result is True, f"Flag-only language {flag_only} should fail safe"


def test_is_search_active_handles_non_string_desired_language_types():
    """Non-string types for desired_language should not crash"""
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    attempts = "[['en', 1609459200]]"
    
    # Various non-string types
    for non_string in [None, 0, 1, 42, -1, 3.14, [], {}, object()]:
        result = module.is_search_active(non_string, attempts)
        assert result is True, f"Non-string desired_language {type(non_string).__name__} should fail safe"
        
        updated = module.updateFailedAttempts(non_string, attempts)
        parsed = ast.literal_eval(updated)
        assert isinstance(parsed, list), f"updateFailedAttempts should not crash on {type(non_string).__name__}"
        assert all(item[0] != "" for item in parsed)


def test_update_failed_attempts_does_not_introduce_empty_language_keys():
    module = _load_adaptive_module()
    attempts = "[['en', 100], ['fr:hi', 200]]"

    updated = module.updateFailedAttempts(None, attempts)
    parsed = ast.literal_eval(updated)

    assert all(item[0] != "" for item in parsed)
    assert any(item[0] == "en" for item in parsed)
    assert any(item[0] == "fr" for item in parsed)


def test_is_search_active_handles_whitespace_only_language():
    """Desired language with only whitespace should fail safe"""
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    now_ts = datetime.timestamp(datetime.now())
    attempts = f"[['en', {now_ts}]]"
    
    for whitespace_lang in ["", " ", "  ", "\t", "\n"]:
        result = module.is_search_active(whitespace_lang, attempts)
        assert result is True, f"Whitespace-only language should fail safe"


def test_is_search_active_matches_legacy_suffixed_attempt_rows_by_base_language():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    now_ts = datetime.timestamp(datetime.now())
    four_weeks_ago = now_ts - (28 * 24 * 3600)
    two_days_ago = now_ts - (2 * 24 * 3600)

    # Legacy rows may store suffixed languages directly instead of base-only languages.
    attempts = f"[['en:forced', {four_weeks_ago}], ['en:hi', {two_days_ago}]]"

    # Initial is outside delay and latest is too recent for delta -> search should be blocked.
    assert module.is_search_active("en", attempts) is False


def test_update_failed_attempts_compacts_non_target_languages_to_initial_and_latest():
    module = _load_adaptive_module()

    # en is target. fr/de should be compacted to initial+latest only.
    attempts = str([
        ["fr", 10],
        ["fr:hi", 20],
        ["fr:forced", 30],
        ["de", 100],
        ["de", 200],
        ["en", 1000],
        ["en:hi", 1100],
    ])
    updated = module.updateFailedAttempts("en", attempts)
    parsed = ast.literal_eval(updated)

    fr_rows = [row for row in parsed if row[0] == "fr"]
    de_rows = [row for row in parsed if row[0] == "de"]

    assert fr_rows == [["fr", 10], ["fr", 30]]
    assert de_rows == [["de", 100], ["de", 200]]


def test_is_search_active_matches_attempts_case_insensitively():
    module = _load_adaptive_module()
    module.settings.general.adaptive_searching = True
    module.settings.general.adaptive_searching_delay = "1w"
    module.settings.general.adaptive_searching_delta = "1w"

    now_ts = datetime.timestamp(datetime.now())
    four_weeks_ago = now_ts - (28 * 24 * 3600)
    two_days_ago = now_ts - (2 * 24 * 3600)
    attempts = f"[['EN', {four_weeks_ago}], ['EN:HI', {two_days_ago}]]"

    # Initial is outside delay and latest is too recent for delta -> search should be blocked.
    assert module.is_search_active("en", attempts) is False
