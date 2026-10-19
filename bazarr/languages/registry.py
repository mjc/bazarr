# coding=utf-8

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias, TypedDict

from subzero.language import Language

from .custom_lang import CustomLanguage


class LanguageEntry(TypedDict):
    code2: str | None
    code3: str | None
    code3b: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class ResolvedLanguage:
    code2: str
    code3: str
    name: str
    code3b: str | None = None

    def to_language(self, *, forced: bool = False, hi: bool = False) -> Language:
        custom = CustomLanguage.from_value(self.code2, 'alpha2')
        language = custom.subzero_language() if custom else Language(self.code3)
        language.forced = forced
        language.hi = hi
        return language


@dataclass(frozen=True, slots=True)
class IncompleteLanguage:
    code2: str | None
    code3: str | None
    name: str | None
    code3b: str | None = None


LanguageRecord: TypeAlias = ResolvedLanguage | IncompleteLanguage


@dataclass(frozen=True, slots=True, init=False)
class LanguageRegistry:
    by_code2: Mapping[str, LanguageRecord]
    by_code3: Mapping[str, LanguageRecord]
    by_name: Mapping[str, LanguageRecord]

    def __init__(self, entries: Iterable[LanguageEntry]) -> None:
        code2: dict[str, LanguageRecord] = {}
        code3: dict[str, LanguageRecord] = {}
        names: dict[str, LanguageRecord] = {}
        for entry in entries:
            first, third, name, alias = entry['code2'], entry['code3'], entry['name'], entry['code3b']
            record = ResolvedLanguage(first, third, name, alias) if first and third and name else IncompleteLanguage(first, third, name, alias)
            if first is not None:
                code2.setdefault(first, record)
            if third is not None:
                code3.setdefault(third, record)
            if alias is not None:
                code3.setdefault(alias, record)
            if name is not None:
                names.setdefault(name, record)
        object.__setattr__(self, 'by_code2', MappingProxyType(code2))
        object.__setattr__(self, 'by_code3', MappingProxyType(code3))
        object.__setattr__(self, 'by_name', MappingProxyType(names))

    def from_code2(self, code: str) -> LanguageRecord | None:
        return self.by_code2.get(code[:2])

    def from_code3(self, code: str) -> LanguageRecord | None:
        return self.by_code3.get(code[:3])

    def from_name(self, name: str) -> LanguageRecord | None:
        return self.by_name.get(name)

    def resolve(self, code: str | None) -> ResolvedLanguage | None:
        record = self.from_code2(code) if code else None
        return record if isinstance(record, ResolvedLanguage) else None
