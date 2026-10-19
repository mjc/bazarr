# coding=utf-8
from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationRequest:
    source_srt_file: str
    dest_srt_file: str
    target_language: str
    to_lang: str
    from_lang: str
    media_type: str
    video_path: str
    orig_to_lang: str
    forced: bool
    hi: bool
    sonarr_series_id: int | None
    sonarr_episode_id: int | None
    radarr_id: int | None
