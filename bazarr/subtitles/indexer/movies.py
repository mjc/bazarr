# coding=utf-8

import gc
import os
import logging
import json

from subliminal_patch import core, search_external_subtitles

from languages.custom_lang import CustomLanguage
from app.database import get_profiles_list, get_profile_cutoff, TableMovies, get_audio_profile_languages, database, \
    update, select, TableMoviesSubtitles, get_subtitles, delete, insert
from languages.get_languages import alpha2_from_alpha3, get_language_set
from app.config import settings
from utilities.helper import get_subtitle_destination_folder
from utilities.path_mappings import path_mappings
from utilities.video_analyzer import embedded_subs_reader
from app.event_handler import event_stream
from subtitles.indexer.utils import guess_external_subtitles, get_external_subtitles_path
from app.jobs_queue import jobs_queue

gc.enable()


def _get_movie_subtitles_scan_paths(mapped_path):
    video_folder = os.path.dirname(mapped_path)
    dest_folder = get_subtitle_destination_folder()
    full_dest_folder_path = video_folder

    if dest_folder:
        if settings.general.subfolder == "absolute":
            full_dest_folder_path = dest_folder
        elif settings.general.subfolder == "relative":
            full_dest_folder_path = os.path.join(video_folder, dest_folder)

    scan_paths = [video_folder]
    if full_dest_folder_path != video_folder:
        scan_paths.append(full_dest_folder_path)

    return dest_folder, full_dest_folder_path, tuple(scan_paths)


def _get_movie_subtitles_scan_signature(scan_paths):
    return json.dumps(
        {
            "ignore_ass_subs": settings.general.ignore_ass_subs,
            "ignore_pgs_subs": settings.general.ignore_pgs_subs,
            "ignore_vobsub_subs": settings.general.ignore_vobsub_subs,
            "single_language": settings.general.single_language,
            "subfolder": settings.general.subfolder,
            "subfolder_custom": settings.general.subfolder_custom,
            "use_embedded_subs": settings.general.use_embedded_subs,
            "paths": [
                [path, os.stat(path).st_mtime_ns if os.path.exists(path) else None]
                for path in scan_paths
            ],
        },
        separators=(',', ':'),
        sort_keys=True,
    )


def _should_skip_full_scan_movie(movie, mapped_path, scan_signature_cache):
    if movie.missing_subtitles != '[]':
        return False

    if not movie.has_indexed_subtitles:
        return False

    if movie.path != movie.subtitles_last_indexed_path:
        return False

    if movie.movie_file_id != movie.subtitles_last_indexed_movie_file_id:
        return False

    if movie.file_size != movie.subtitles_last_indexed_file_size:
        return False

    if not movie.subtitles_last_indexed_external_signature:
        return False

    if not os.path.exists(mapped_path):
        return False

    _, _, scan_paths = _get_movie_subtitles_scan_paths(mapped_path)
    scan_signature = scan_signature_cache.get(scan_paths)
    if scan_signature is None:
        scan_signature = _get_movie_subtitles_scan_signature(scan_paths)
        scan_signature_cache[scan_paths] = scan_signature

    return scan_signature == movie.subtitles_last_indexed_external_signature


def store_subtitles_movie(radarr_id, use_cache=True, item=None):
    if item is None:
        item = database.execute(
            select(TableMovies.radarrId,
                   TableMovies.path,
                   TableMovies.movie_file_id,
                   TableMovies.file_size)
            .where(TableMovies.radarrId == radarr_id)
        ).first()

    if not item:
        logging.warning(f"BAZARR could not find movie with ID {radarr_id} in the database.")
        return
    else:
        original_path = item.path
        mapped_path = path_mappings.path_replace_movie(original_path)
        index_complete = True
        scan_paths = ()

    logging.debug(f'BAZARR started subtitles indexing for this file: {mapped_path}')
    embedded_subtitles = []
    external_subtitles = []

    if os.path.exists(mapped_path):
        if settings.general.use_embedded_subs:
            logging.debug("BAZARR is trying to index embedded subtitles.")
            try:
                # Get all embedded subtitles
                subtitle_languages = embedded_subs_reader(mapped_path,
                                                          file_size=item.file_size,
                                                          movie_file_id=item.movie_file_id,
                                                          use_cache=use_cache)
                for track_id, subtitle_language, subtitle_forced, subtitle_hi, subtitle_codec in subtitle_languages:
                    try:
                        # Skip subtitles track using codecs that the user doesn't want to index
                        if (settings.general.ignore_pgs_subs and subtitle_codec.lower() == "pgs") or \
                                (settings.general.ignore_vobsub_subs and subtitle_codec.lower() ==
                                 "vobsub") or \
                                (settings.general.ignore_ass_subs and subtitle_codec.lower() ==
                                 "ass"):
                            logging.debug(f"BAZARR skipping {subtitle_codec} sub for language: "
                                          f"{alpha2_from_alpha3(subtitle_language)}")
                            continue

                        # Index embedded subtitles with defined and supported language
                        if alpha2_from_alpha3(subtitle_language) is not None:
                            lang = alpha2_from_alpha3(subtitle_language)
                            logging.debug(f"BAZARR embedded subtitles detected: {lang}"
                                          f"{':forced' if subtitle_forced else ''}{':hi' if subtitle_hi else ''}")
                            embedded_subtitles.append({'radarrId': item.radarrId,
                                                       'language': lang,
                                                       'forced': subtitle_forced,
                                                       'hi': subtitle_hi,
                                                       'embedded_track_id': track_id})
                    except Exception as error:
                        logging.debug(f"BAZARR unable to index this unrecognized language: {subtitle_language} "
                                      f"({error})")

                database.execute(
                    # Delete prior indexed embedded subtitles lacking track ID
                    delete(TableMoviesSubtitles)
                    .where(TableMoviesSubtitles.radarrId == item.radarrId)
                    .where(TableMoviesSubtitles.path.is_(None))
                    .where(TableMoviesSubtitles.embedded_track_id.is_(None))
                )
                if len(embedded_subtitles):
                    # Insert new embedded subtitles or update existing ones
                    embedded_stmt = insert(TableMoviesSubtitles).values(embedded_subtitles)
                    embedded_stmt = embedded_stmt.on_conflict_do_update(
                        index_elements=['embedded_track_id', 'radarrId', 'language', 'forced', 'hi'],
                        set_={
                            'language': embedded_stmt.excluded.language,
                            'forced': embedded_stmt.excluded.forced,
                            'hi': embedded_stmt.excluded.hi,
                            'size': embedded_stmt.excluded.size,
                            'embedded_track_id': embedded_stmt.excluded.embedded_track_id
                        },
                        index_where=TableMoviesSubtitles.path.is_(None)
                    )
                    database.execute(embedded_stmt)

                    # Delete prior indexed embedded subtitles that don't exist anymore
                    embedded_subtitles_id_list = [x['embedded_track_id'] for x in embedded_subtitles]
                    if len(embedded_subtitles_id_list):
                        database.execute(
                            delete(TableMoviesSubtitles)
                            .where(TableMoviesSubtitles.radarrId == radarr_id)
                            .where(TableMoviesSubtitles.path.is_(None))
                            .where(TableMoviesSubtitles.embedded_track_id.not_in(embedded_subtitles_id_list))
                        )
            except Exception:
                index_complete = False
                logging.exception(
                    f"BAZARR error when trying to analyze this {os.path.splitext(mapped_path)[1]} file: "
                    f"{mapped_path}")
                pass

        try:
            dest_folder, full_dest_folder_path, scan_paths = _get_movie_subtitles_scan_paths(mapped_path)
            core.CUSTOM_PATHS = [dest_folder] if dest_folder else []

            # get previously indexed subtitles that haven't changed:
            previously_indexed_subtitles = get_subtitles(radarr_id=radarr_id)
            previously_indexed_subtitles_to_exclude = \
                [x for x in previously_indexed_subtitles
                 if x['path'] and
                 os.path.isfile(x['path']) and
                 os.stat(x['path']).st_size == x['file_size']]

            # Get previously indexed subtitles that no longer exist:
            previously_indexed_subtitles_to_delete = \
                [path_mappings.path_replace_reverse_movie(x['path']) for x in previously_indexed_subtitles
                 if x['path'] and not os.path.isfile(x['path'])]

            if previously_indexed_subtitles_to_delete:
                database.execute(
                    delete(TableMoviesSubtitles)
                    .where(TableMoviesSubtitles.path.in_(previously_indexed_subtitles_to_delete)))

            # Search for external subtitles:
            subtitles = search_external_subtitles(mapped_path, languages=get_language_set(),
                                                  only_one=settings.general.single_language)

            # Guess external subtitles language if not specified in the file name:
            subtitles = guess_external_subtitles(full_dest_folder_path, subtitles,
                                                 previously_indexed_subtitles_to_exclude)
        except Exception as e:
            index_complete = False
            logging.exception(f"BAZARR unable to index external subtitles for this file {mapped_path}: {repr(e)}")
        else:
            # For each external subtitle, store it in the database
            for subtitle, language in subtitles.items():
                valid_language = False
                if language:
                    if hasattr(language, 'alpha3'):
                        valid_language = alpha2_from_alpha3(language.alpha3)
                else:
                    logging.debug(f"Skipping subtitles because we are unable to define language: {subtitle}")
                    continue

                if not valid_language:
                    logging.debug(f'{language.alpha3} is an unsupported language code.')
                    continue

                subtitle_path = get_external_subtitles_path(mapped_path, subtitle)

                try:
                    subtitle_size = os.stat(subtitle_path).st_size
                except FileNotFoundError:
                    logging.debug(f"BAZARR skipping missing subtitle file: {subtitle_path}")
                    continue

                # We get custom language external subtitles
                custom = CustomLanguage.found_external(subtitle, subtitle_path)
                if custom is not None:
                    logging.debug(f"BAZARR external subtitles detected: {custom}")
                    external_subtitles.append({'radarrId': item.radarrId,
                                               'language': custom.split(':')[0],
                                               'forced': custom.endswith(':forced'),
                                               'hi': custom.endswith(':hi'),
                                               'path': path_mappings.path_replace_reverse_movie(subtitle_path),
                                               'size': subtitle_size})

                # We get defined and supported language external subtitles
                elif str(language.basename) != 'und':
                    logging.debug(f"BAZARR external subtitles detected: {language}"
                                  f"{':forced' if language.forced else ''}"
                                  f"{':hi' if language.hi else ''}")
                    external_subtitles.append({'radarrId': item.radarrId,
                                               'language': language.basename,
                                               'forced': language.forced,
                                               'hi': language.hi,
                                               'path': path_mappings.path_replace_reverse_movie(subtitle_path),
                                               'size': subtitle_size})

            # We store external subtitles in the database or update existing ones
            if len(external_subtitles):
                stmt = insert(TableMoviesSubtitles).values(external_subtitles)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['path', 'radarrId', 'language', 'forced', 'hi'],
                    set_={
                        'language': stmt.excluded.language,
                        'forced': stmt.excluded.forced,
                        'hi': stmt.excluded.hi,
                        'size': stmt.excluded.size
                    }
                )
                database.execute(stmt)
    else:
        logging.debug("BAZARR this file doesn't seems to exist or isn't accessible.")
        return

    # We store actual subtitles for this episode in the database
    logging.debug(f"BAZARR storing those languages to DB: {embedded_subtitles + external_subtitles}")

    # We list missing subtitles for this movie and store them in the database
    list_missing_subtitles_movies(no=radarr_id)

    if index_complete:
        database.execute(
            update(TableMovies)
            .values(
                subtitles_last_indexed_external_signature=_get_movie_subtitles_scan_signature(scan_paths),
                subtitles_last_indexed_file_size=item.file_size,
                subtitles_last_indexed_movie_file_id=item.movie_file_id,
                subtitles_last_indexed_path=item.path,
            )
            .where(TableMovies.radarrId == radarr_id)
        )

    logging.debug(f'BAZARR ended subtitles indexing for this file: {mapped_path}')


def list_missing_subtitles_movies(no=None):
    stmt = select(TableMovies.radarrId,
                  TableMovies.profileId,
                  TableMovies.audio_language)

    if no:
        movies_subtitles = database.execute(stmt.where(TableMovies.radarrId == no)).all()
    else:
        movies_subtitles = database.execute(stmt).all()

    use_embedded_subs = settings.general.use_embedded_subs

    matches_audio = lambda language: any(x['code2'] == language['language'] for x in get_audio_profile_languages(
                                movie_subtitles.audio_language))

    for movie_subtitles in movies_subtitles:
        missing_subtitles_text = '[]'
        if movie_subtitles.profileId:
            # get desired subtitles
            desired_subtitles_temp = get_profiles_list(profile_id=movie_subtitles.profileId)
            desired_subtitles_list = []
            if desired_subtitles_temp:
                for language in desired_subtitles_temp['items']:
                    if language['audio_exclude'] == "True":
                        if matches_audio(language):
                            continue
                    if language['audio_only_include'] == "True":
                        if not matches_audio(language):
                            continue
                    desired_subtitles_list.append({'language': language['language'],
                                                   'forced': str(language['forced']),
                                                   'hi': str(language['hi'])})

            # get existing subtitles
            actual_subtitles_list = []
            actual_subtitles_temp = get_subtitles(radarr_id=movie_subtitles.radarrId)
            if not use_embedded_subs:
                actual_subtitles_temp = [x for x in actual_subtitles_temp if x['path']]

            for subtitles in actual_subtitles_temp:
                actual_subtitles_list.append({'language': subtitles['code2'],
                                              'forced': str(subtitles['forced']),
                                              'hi': str(subtitles['hi'])})

            # check if cutoff is reached and skip any further check
            cutoff_met = False
            cutoff_temp_list = get_profile_cutoff(profile_id=movie_subtitles.profileId)

            if cutoff_temp_list:
                for cutoff_temp in cutoff_temp_list:
                    cutoff_language = {'language': cutoff_temp['language'],
                                       'forced': cutoff_temp['forced'],
                                       'hi': cutoff_temp['hi']}
                    if cutoff_temp['audio_only_include'] == 'True' and not matches_audio(cutoff_temp):
                        # We don't want subs in this language unless it matches
                        # the audio. Don't use it to meet the cutoff.
                        continue
                    elif cutoff_temp['audio_exclude'] == 'True' and matches_audio(cutoff_temp):
                        # The cutoff is met through one of the audio tracks.
                        cutoff_met = True
                    elif cutoff_language in actual_subtitles_list:
                        cutoff_met = True
                    # HI is considered as good as normal
                    elif (cutoff_language and
                          {'language': cutoff_language['language'],
                           'forced': 'False',
                           'hi': 'True'} in actual_subtitles_list):
                        cutoff_met = True

            if cutoff_met:
                missing_subtitles_text = str([])
            else:
                # get difference between desired and existing subtitles
                missing_subtitles_list = []
                for item in desired_subtitles_list:
                    if item not in actual_subtitles_list:
                        missing_subtitles_list.append(item)

                # remove missing that have forced or hi subtitles for this language in existing
                for item in actual_subtitles_list:
                    if item['hi'] == 'True':
                        try:
                            missing_subtitles_list.remove({'language': item['language'],
                                                           'forced': 'False',
                                                           'hi': 'False'})
                        except ValueError:
                            pass

                # make the missing languages list looks like expected
                missing_subtitles_output_list = []
                for item in missing_subtitles_list:
                    lang = item['language']
                    if item['forced'] == 'True':
                        lang += ':forced'
                    elif item['hi'] == 'True':
                        lang += ':hi'
                    missing_subtitles_output_list.append(lang)

                missing_subtitles_text = str(missing_subtitles_output_list)

        database.execute(
            update(TableMovies)
            .values(missing_subtitles=missing_subtitles_text)
            .where(TableMovies.radarrId == movie_subtitles.radarrId))

        event_stream(type='movie', payload=movie_subtitles.radarrId)
        event_stream(type='movie-wanted', action='update', payload=movie_subtitles.radarrId)
    event_stream(type='badges')


def movies_full_scan_subtitles(job_id=None, use_cache=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Indexing all existing movies subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return

    if use_cache is None:
        use_cache = settings.radarr.use_ffprobe_cache

    movies = database.execute(
        select(TableMovies.path,
               TableMovies.file_size,
               TableMovies.missing_subtitles,
               TableMovies.movie_file_id,
               TableMovies.radarrId,
               TableMovies.subtitles_last_indexed_external_signature,
               TableMovies.subtitles_last_indexed_file_size,
               TableMovies.subtitles_last_indexed_movie_file_id,
               TableMovies.subtitles_last_indexed_path,
               TableMovies.title,
               select(TableMoviesSubtitles.id)
               .where(TableMoviesSubtitles.radarrId == TableMovies.radarrId)
               .limit(1)
               .exists()
               .label("has_indexed_subtitles")))\
        .all()

    jobs_queue.update_job_progress(job_id=job_id, progress_max=len(movies), progress_message='Indexing')
    scan_signature_cache = {}
    for i, movie in enumerate(movies, start=1):
        jobs_queue.update_job_progress(job_id=job_id, progress_value=i, progress_message=movie.title)
        mapped_path = path_mappings.path_replace_movie(movie.path)
        if _should_skip_full_scan_movie(movie, mapped_path, scan_signature_cache):
            continue
        store_subtitles_movie(movie.radarrId, use_cache=use_cache, item=movie)

    logging.info('BAZARR All existing movie subtitles indexed from disk.')

    jobs_queue.update_job_name(job_id=job_id, new_job_name="Indexed all existing movies subtitles")

    gc.collect()


def movies_scan_subtitles(no):
    movies = database.execute(
        select(TableMovies.radarrId)
        .where(TableMovies.radarrId == no)
        .order_by(TableMovies.radarrId)) \
        .all()

    for movie in movies:
        store_subtitles_movie(movie.radarrId, use_cache=False)
