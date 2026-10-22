# coding=utf-8

from sqlalchemy import select

from app.database import TableShows, database
from languages.get_languages import audio_language_from_name
from sonarr.episode_snapshot import Episode, EpisodeFile
from utilities.path_mappings import path_mappings
from utilities.video_analyzer import embedded_audio_reader


def episode_audio_language(episode: Episode, file: EpisodeFile, parse_embedded_audio_track: bool,
                           *, series_languages: dict[int, str]) -> str:
    if parse_embedded_audio_track:
        return str(embedded_audio_reader(path_mappings.path_replace(file.path),
                                        file_size=file.size, episode_file_id=file.id, use_cache=True))
    if file.language:
        return str([audio_language_from_name(file.language.name)])
    if file.languages:
        return str([audio_language_from_name(language.name) for language in file.languages])
    series_id = episode.seriesId
    if series_id not in series_languages:
        series_languages[series_id] = str(database.execute(select(TableShows.audio_language)
                                                          .where(TableShows.sonarrSeriesId == series_id)).scalar())
    return series_languages[series_id]
