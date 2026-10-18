from dataclasses import dataclass

import pytest

from guessit import guessit
from rebulk import Rebulk
from rebulk.pattern import StringPattern


@dataclass
class UnhashablePattern(StringPattern):
    text: str

    def __post_init__(self) -> None:
        super().__init__(self.text)


def test_effective_patterns_preserve_order_and_root_duplicates() -> None:
    first = StringPattern('first')
    second = StringPattern('second')
    third = StringPattern('third')
    root = Rebulk().pattern(first, first)
    root.rebulk(Rebulk().pattern(first, second, second), Rebulk().pattern(second, third))

    patterns = root.effective_patterns()

    assert len(patterns) == 4
    assert all(actual is expected for actual, expected in zip(patterns, [first, first, second, third]))


def test_distinct_pattern_instances_are_not_combined() -> None:
    first = StringPattern('same')
    second = StringPattern('same')
    root = Rebulk().pattern(first).rebulk(Rebulk().pattern(second))

    patterns = root.effective_patterns()

    assert len(patterns) == 2
    assert patterns[0] is first
    assert patterns[1] is second


@pytest.mark.parametrize('in_root', [False, True])
def test_unhashable_patterns_keep_equality_based_deduplication(in_root: bool) -> None:
    first = UnhashablePattern('same')
    duplicate = UnhashablePattern('same')
    last = StringPattern('last')
    root = Rebulk()
    if in_root:
        root.pattern(first)
    else:
        root.rebulk(Rebulk().pattern(first))
    root.rebulk(Rebulk().pattern(duplicate, last))

    patterns = root.effective_patterns()

    assert len(patterns) == 2
    assert patterns[0] is first
    assert patterns[1] is last


def test_context_changes_and_new_patterns_are_observed() -> None:
    first = StringPattern('first')
    second = StringPattern('second')
    child = Rebulk(disabled=lambda context: context.get('disabled', False)).pattern(first)
    root = Rebulk().rebulk(child)

    assert root.effective_patterns({'disabled': True}) == []
    assert root.effective_patterns({'disabled': False}) == [first]
    child.pattern(second)
    assert root.effective_patterns({'disabled': False}) == [first, second]


def test_disabled_callbacks_run_when_their_child_is_visited() -> None:
    first = StringPattern('first')
    second = StringPattern('second')
    child = Rebulk().pattern(first)

    def add_pattern(context: dict[str, bool] | None) -> bool:
        child.pattern(second)
        return False

    root = Rebulk().rebulk(child, Rebulk(disabled=add_pattern))

    assert root.effective_patterns() == [first]


@pytest.mark.parametrize(('filename', 'options', 'expected'), [
    ('Example.Show.S02E03.1080p.WEB-DL.x264-GROUP.mkv', {'type': 'episode'},
     {'title': 'Example Show', 'season': 2, 'episode': 3, 'screen_size': '1080p'}),
    ('Example.Movie.2020.720p.BluRay.x264-GROUP.mkv', {'type': 'movie'},
     {'title': 'Example Movie', 'year': 2020, 'screen_size': '720p'}),
])
def test_guessit_results(filename: str, options: dict[str, str],
                        expected: dict[str, str | int]) -> None:
    result = guessit(filename, options)

    assert {key: result[key] for key in expected} == expected
