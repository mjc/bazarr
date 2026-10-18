# coding=utf-8
from collections.abc import Hashable, Iterable
from typing import TypeVar

_T = TypeVar('_T', bound=Hashable)


def unique_in_order(values: Iterable[_T]) -> list[_T]:
    """Keep the first occurrence of each hashable value."""
    return list(dict.fromkeys(values))
