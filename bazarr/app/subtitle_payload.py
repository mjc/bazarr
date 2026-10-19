from collections.abc import Callable, Iterable
from typing import Protocol, TypedDict


class SubtitleRow(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def path(self) -> str | None: ...

    @property
    def language(self) -> str: ...

    @property
    def forced(self) -> bool: ...

    @property
    def hi(self) -> bool: ...

    @property
    def size(self) -> int | None: ...

    @property
    def embedded_track_id(self) -> int | None: ...


class SubtitlePayload(TypedDict):
    id: int
    path: str | None
    name: str | None
    code2: str
    code3: str | None
    forced: bool
    hi: bool
    file_size: int | None
    embedded_track_id: int | None


def subtitle_payload(
    subtitle: SubtitleRow,
    replace_path: Callable[[str | None], str | None],
) -> SubtitlePayload:
    from languages.get_languages import alpha3_from_alpha2, language_from_alpha2

    return {"id": subtitle.id,
            "path": replace_path(subtitle.path),
            "name": language_from_alpha2(subtitle.language),
            "code2": subtitle.language,
            "code3": alpha3_from_alpha2(subtitle.language),
            "forced": subtitle.forced,
            "hi": subtitle.hi,
            "file_size": subtitle.size,
            "embedded_track_id": subtitle.embedded_track_id}


def sort_subtitles(subtitles: Iterable[SubtitlePayload]) -> list[SubtitlePayload]:
    return sorted(subtitles, key=lambda i: (i['name'] or '', i['forced']))
