from collections.abc import Iterator

import pytest

from utilities.collections import unique_in_order


@pytest.mark.parametrize(('values', 'expected'), [
    ([], []),
    (['English', 'English', None, 'French', None], ['English', None, 'French']),
    ([None, 'English', None, 'French'], [None, 'English', 'French']),
    ([3, 1, 3, 2, 1], [3, 1, 2]),
    ([True, 1, False, 0, None], [True, False, None]),
])
def test_unique_in_order(values: list[str | int | None], expected: list[str | int | None]) -> None:
    original = list(values)

    assert unique_in_order(values) == expected
    assert values == original


def test_unique_in_order_consumes_a_generator_once() -> None:
    visited: list[int] = []

    def values() -> Iterator[int]:
        for value in [3, 1, 3, 2]:
            visited.append(value)
            yield value

    assert unique_in_order(values()) == [3, 1, 2]
    assert visited == [3, 1, 3, 2]


@pytest.mark.parametrize(('values', 'coerce_int', 'expected'), [
    ([3, 1, 3, 2], False, [3, 1, 2]),
    (['2', 2, '1', 1], True, [2, 1]),
    (7, False, [7]),
    ('7', False, ['7']),
    ('7', True, [7]),
    ([], True, []),
])
def test_media_id_normalization_preserves_deduplication(values: int | str | list[int | str],
                                                       coerce_int: bool,
                                                       expected: list[int | str]) -> None:
    from subtitles.wanted_state import _normalize_media_ids

    assert _normalize_media_ids(values, coerce_int=coerce_int) == expected
