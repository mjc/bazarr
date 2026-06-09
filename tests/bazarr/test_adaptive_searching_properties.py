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

        # Non-target entries should be preserved exactly
        before_non_target = Counter(tuple(item) for item in attempts if item[0] != desired_language)
        after_non_target = Counter(tuple(item) for item in parsed if item[0] != desired_language)
        assert before_non_target == after_non_target

        before_target = [item for item in attempts if item[0] == desired_language]
        after_target = [item for item in parsed if item[0] == desired_language]

        # If target existed, keep oldest + new; otherwise add one new.
        assert len(after_target) == (2 if before_target else 1)
        if before_target:
            oldest = min(item[1] for item in before_target)
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
