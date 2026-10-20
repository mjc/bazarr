# coding=utf-8

import os
from dataclasses import dataclass

from app.config import settings


def subtitle_directories(video_path: str) -> tuple[str, ...]:
    general = settings.general
    alongside = os.path.dirname(video_path)
    mode = general.subfolder
    if mode == 'current':
        return (alongside,)
    if mode not in ('absolute', 'relative'):
        return ()
    custom = str(general.subfolder_custom or '').strip()
    if not custom:
        return (alongside,)
    directory = os.path.join(alongside, custom) if mode == 'relative' else custom
    return (alongside, directory) if directory != alongside else (alongside,)


@dataclass(frozen=True, slots=True)
class ExternalSubtitle:
    path: str
    size: int


def find_external_subtitle(video_path: str, subtitle: str) -> ExternalSubtitle | None:
    for directory in subtitle_directories(video_path):
        path = os.path.join(directory, subtitle)
        try:
            metadata = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        return ExternalSubtitle(path, metadata.st_size)
    return None


def subtitle_destination(video_path: str, subtitle: str) -> str | None:
    directories = subtitle_directories(video_path)
    return os.path.join(directories[-1], subtitle) if directories else None
