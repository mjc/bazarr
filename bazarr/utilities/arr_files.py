# coding=utf-8

import os
from collections.abc import Callable

from app.config import settings
from constants import MINIMUM_VIDEO_SIZE


class FileSizes:
    def __init__(self, path_replace: Callable[[str], str]) -> None:
        self._path_replace = path_replace
        self._sizes: dict[str, int] = {}

    def resolve(self, path: str, reported_size: int) -> int:
        if reported_size > MINIMUM_VIDEO_SIZE:
            return reported_size
        if path not in self._sizes:
            try:
                self._sizes[path] = os.path.getsize(self._path_replace(path))
            except OSError:
                self._sizes[path] = 0
        return self._sizes[path]


def is_usable_file(path: str, size: int) -> bool:
    return (size > MINIMUM_VIDEO_SIZE
            or bool(settings.general.enable_strm_support and path.lower().endswith('.strm')))
