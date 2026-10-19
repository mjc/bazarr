# coding=utf-8

from typing import TypedDict

from languages.formatting import LanguageInfo


class SubtitleFile(LanguageInfo):
    id: int
    path: str | None
    forced: bool
    hi: bool
    file_size: int | None
    embedded_track_id: int | None


class SubtitleOrder(TypedDict):
    name: str | None
    forced: bool


def subtitle_sort_key(subtitle: SubtitleOrder) -> tuple[str | None, bool]:
    return subtitle['name'], subtitle['forced']
