from collections.abc import Iterator
from typing import TypeAlias

import pytest

from guessit.options import merge_options
from rebulk import Rebulk
from rebulk.introspector import introspect
from rebulk.processors import ConflictSolver, PrivateRemover
from rebulk.utils import extend_safe

DedupValue: TypeAlias = str | int | None | list[int] | dict[str, int] | set[int] | frozenset[int]


@pytest.mark.parametrize(('target', 'source', 'expected'), [
    ([1, 1, 2], [2, 3, 3, 4], [1, 1, 2, 3, 4]),
    ([[1]], [[1], [2], 3, 3], [[1], [2], 3]),
    ([1], [2, [3], [3], 2, 4, 4], [1, 2, [3], 4]),
    ([frozenset({1})], [{1}, {2}, frozenset({2})], [frozenset({1}), {2}]),
    ([None, True], [1, None, 0, False], [None, True, 0]),
    ([], [{'x': 1}, {'x': 1}, {'x': 2}], [{'x': 1}, {'x': 2}]),
])
def test_extend_safe_keeps_list_equality_and_order(target: list[DedupValue], source: list[DedupValue],
                                                  expected: list[DedupValue]) -> None:
    assert extend_safe(target, source) is None
    assert target == expected


def test_extend_safe_consumes_a_generator_once() -> None:
    target: list[DedupValue] = [1]
    source: list[DedupValue] = [2, [3], [3], 4]
    visited: list[DedupValue] = []

    def values() -> Iterator[DedupValue]:
        for value in source:
            visited.append(value)
            yield value

    extend_safe(target, values())

    assert target == [1, 2, [3], 4]
    assert visited == source


def test_extend_safe_accepts_its_target_as_source() -> None:
    target = [1, 1, 2]

    extend_safe(target, target)

    assert target == [1, 1, 2]


def test_effective_rules_keep_parent_duplicates_and_deduplicate_by_class() -> None:
    first = ConflictSolver()
    duplicate = ConflictSolver()
    second = PrivateRemover()
    root = Rebulk(default_rules=False).rules(first, first)
    root.rebulk(Rebulk(default_rules=False).rules(duplicate, second, second))

    rules = root.effective_rules()

    assert len(rules) == 3
    assert rules[0] is first
    assert rules[1] is first
    assert rules[2] is second


def test_rule_callbacks_keep_traversal_order_and_observe_later_changes() -> None:
    first = ConflictSolver()
    second = PrivateRemover()
    child = Rebulk(default_rules=False).rules(first)

    def add_rule(context: dict[str, bool] | None) -> bool:
        child.rules(second)
        return bool(context and context.get('disabled'))

    root = Rebulk(default_rules=False).rebulk(child, Rebulk(disabled=add_rule))

    rules = root.effective_rules({'disabled': True})
    assert len(rules) == 1 and rules[0] is first
    rules = root.effective_rules({'disabled': True})
    assert len(rules) == 2 and rules[1] is second


def test_introspection_keeps_unhashable_property_values_in_order() -> None:
    bulk = Rebulk(default_rules=False)
    bulk.string('first', properties={'items': [[1], 'a']})
    bulk.string('second', properties={'items': [[1], [2], 'a', 'b']})
    rule = ConflictSolver()
    rule.properties = {'items': [[2], 'c']}
    bulk.rules(rule)

    description = introspect(bulk)
    assert description.properties['items'] == [[1], 'a', [2], 'b', 'c']
    assert description.properties['items'] == [[1], 'a', [2], 'b', 'c']


def test_option_merging_preserves_existing_duplicates_and_none_values() -> None:
    initial = {'languages': ['en', 'en', None]}

    result = merge_options(initial, {'languages': ['en', None, 'fr', 'fr']})

    assert result == {'languages': ['en', 'en', None, 'fr']}
    assert initial == {'languages': ['en', 'en', None]}


def test_option_merging_supports_unhashable_nested_values() -> None:
    result = merge_options({'nested': {'values': [{'id': 1}]}},
                           {'nested': {'values': [{'id': 1}, {'id': 2}, None]}})

    assert result == {'nested': {'values': [{'id': 1}, {'id': 2}]}}
