# coding=utf-8

from languages.get_languages import audio_language_from_name
from radarr.movie_snapshot import Movie, MovieFile
from utilities.arr_sync_lookups import ArrSyncLookups
from utilities.path_mappings import path_mappings
from utilities.video_analyzer import embedded_audio_reader


def movie_audio_language(movie: Movie, file: MovieFile, lookups: ArrSyncLookups, *, parse_embedded_audio_track: bool) -> str:
    if parse_embedded_audio_track:
        return str(embedded_audio_reader(path_mappings.path_replace_movie(file.path),
                                        file_size=file.size, movie_file_id=file.id, use_cache=True))
    media = file.mediaInfo
    if media is not None and media.audioLanguages is not None:
        return str([language.strip() for language in media.audioLanguages.split('/')])
    if file.languages:
        return str([audio_language_from_name(language.name) for language in file.languages])
    return lookups.audio_language_values.get(movie.qualityProfileId, '[]')
