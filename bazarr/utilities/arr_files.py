# coding=utf-8

import os
from collections.abc import Callable

from app.config import settings
from constants import MINIMUM_VIDEO_SIZE


def is_usable_file(path: str, reported_size: int, *, path_replace: Callable[[str], str],
                   file_sizes: dict[str, int]) -> bool:
    if reported_size > MINIMUM_VIDEO_SIZE:
        return True
    if path not in file_sizes:
        try:
            file_sizes[path] = os.path.getsize(path_replace(path))
        except OSError:
            file_sizes[path] = 0
    return (file_sizes[path] > MINIMUM_VIDEO_SIZE
            or bool(settings.general.enable_strm_support and path.lower().endswith('.strm')))
