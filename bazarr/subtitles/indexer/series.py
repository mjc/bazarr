# coding=utf-8

import gc
import json
import os
import logging

from subliminal_patch import core, search_external_subtitles

from languages.custom_lang import CustomLanguage
from app.database import get_profiles_list, get_profile_cutoff, TableEpisodes, TableShows, TableEpisodesSubtitles, \
    get_audio_profile_languages, database, update, select, insert, delete
from languages.get_languages import alpha2_from_alpha3, get_language_set
from app.config import settings
from utilities.helper import get_subtitle_destination_folder
from utilities.path_mappings import path_mappings
from utilities.video_analyzer import embedded_subs_reader
from app.event_handler import event_stream
from subtitles.indexer.utils import guess_external_subtitles, get_external_subtitles_path
from subtitles.serialization import dump_text_list
from app.jobs_queue import jobs_queue

gc.enable()


def _get_subtitles_scan_paths(mapped_path):
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


def _get_subtitles_scan_signature(scan_paths):
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


def _should_skip_full_scan_episode(episode, mapped_path, scan_signature_cache):
    if episode.missing_subtitles != '[]':
        return False

    if not episode.has_indexed_subtitles:
        return False

    if episode.path != episode.subtitles_last_indexed_path:
        return False

    if episode.episode_file_id != episode.subtitles_last_indexed_episode_file_id:
        return False

    if episode.file_size != episode.subtitles_last_indexed_file_size:
        return False

    if not episode.subtitles_last_indexed_external_signature:
        return False

    if not os.path.exists(mapped_path):
        return False

    _, _, scan_paths = _get_subtitles_scan_paths(mapped_path)
    scan_signature = scan_signature_cache.get(scan_paths)
    if scan_signature is None:
        scan_signature = _get_subtitles_scan_signature(scan_paths)
        scan_signature_cache[scan_paths] = scan_signature

    return scan_signature == episode.subtitles_last_indexed_external_signature


def _get_indexed_subtitles(sonarr_episode_id):
    return database.execute(
        select(
            TableEpisodesSubtitles.path,
            TableEpisodesSubtitles.size.label("file_size"),
            TableEpisodesSubtitles.language.label("code2"),
            TableEpisodesSubtitles.forced,
            TableEpisodesSubtitles.hi,
            TableEpisodesSubtitles.embedded_track_id,
        )
        .where(TableEpisodesSubtitles.sonarrEpisodeId == sonarr_episode_id)
    ).all()


def _subtitle_entry(language, forced, hi):
    return language, str(forced), str(hi)


def _actual_subtitles_list(indexed_subtitles, use_embedded_subs):
    actual_subtitles = []
    for subtitles in indexed_subtitles:
        if subtitles.get('path') is None and not use_embedded_subs:
            continue

        actual_subtitles.append(_subtitle_entry(subtitles['language'], subtitles['forced'], subtitles['hi']))

    return actual_subtitles


def _get_missing_subtitles_text(profile_id, audio_language, indexed_subtitles):
    if not profile_id:
        return '[]'

    audio_languages = get_audio_profile_languages(audio_language)
    actual_subtitles = set(_actual_subtitles_list(indexed_subtitles, settings.general.use_embedded_subs))

    def matches_audio(language):
        return any(x['code2'] == language['language'] for x in audio_languages)

    desired_subtitles = get_profiles_list(profile_id=profile_id)
    desired_subtitles_list = []
    if desired_subtitles:
        for language in desired_subtitles['items']:
            if language['audio_exclude'] == "True" and matches_audio(language):
                continue
            if language['audio_only_include'] == "True" and not matches_audio(language):
                continue

            desired_subtitles_list.append(
                _subtitle_entry(language['language'], language['forced'], language['hi'])
            )

    cutoff_temp_list = get_profile_cutoff(profile_id=profile_id)
    if cutoff_temp_list:
        for cutoff_temp in cutoff_temp_list:
            cutoff_language = _subtitle_entry(cutoff_temp['language'], cutoff_temp['forced'], cutoff_temp['hi'])
            if cutoff_temp['audio_only_include'] == 'True' and not matches_audio(cutoff_temp):
                continue
            if cutoff_temp['audio_exclude'] == 'True' and matches_audio(cutoff_temp):
                return dump_text_list([])
            if cutoff_language in actual_subtitles:
                return dump_text_list([])
            if _subtitle_entry(cutoff_temp['language'], False, True) in actual_subtitles:
                return dump_text_list([])

    hi_languages = {
        language
        for language, _, hi in actual_subtitles
        if hi == 'True'
    }
    missing_subtitles_output_list = []
    for language, forced, hi in desired_subtitles_list:
        if (language, forced, hi) in actual_subtitles:
            continue
        if forced == 'False' and hi == 'False' and language in hi_languages:
            continue

        missing_language = language
        if forced == 'True':
            missing_language += ':forced'
        elif hi == 'True':
            missing_language += ':hi'
        missing_subtitles_output_list.append(missing_language)

    return dump_text_list(missing_subtitles_output_list)


def store_subtitles(sonarr_episode_id, use_cache=True, item=None, languages=None):
    if item is None:
        item = database.execute(
            select(
                TableEpisodes.sonarrSeriesId,
                TableEpisodes.path,
                TableEpisodes.episode_file_id,
                TableEpisodes.file_size,
                TableShows.profileId,
                TableEpisodes.audio_language,
            )
            .select_from(TableEpisodes)
            .join(TableShows)
            .where(TableEpisodes.sonarrEpisodeId == sonarr_episode_id)
        ).first()

    if not item:
        logging.warning(f"BAZARR could not find episode with ID {sonarr_episode_id} in the database.")
        return
    else:
        original_path = item.path
        mapped_path = path_mappings.path_replace(original_path)
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
                                                          episode_file_id=item.episode_file_id,
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
                            embedded_subtitles.append({'sonarrSeriesId': item.sonarrSeriesId,
                                                       'sonarrEpisodeId': sonarr_episode_id,
                                                       'language': lang,
                                                       'forced': subtitle_forced,
                                                       'hi': subtitle_hi,
                                                       'embedded_track_id': track_id})
                    except Exception as error:
                        logging.debug(f"BAZARR unable to index this unrecognized language: {subtitle_language} "
                                      f"({error})")

                database.execute(
                    # Delete prior indexed embedded subtitles lacking track ID
                    delete(TableEpisodesSubtitles)
                    .where(TableEpisodesSubtitles.sonarrSeriesId == item.sonarrSeriesId)
                    .where(TableEpisodesSubtitles.sonarrEpisodeId == sonarr_episode_id)
                    .where(TableEpisodesSubtitles.path.is_(None))
                    .where(TableEpisodesSubtitles.embedded_track_id.is_(None))
                )
                if len(embedded_subtitles):
                    # Insert new embedded subtitles or update existing ones
                    embedded_stmt = insert(TableEpisodesSubtitles).values(embedded_subtitles)
                    embedded_stmt = embedded_stmt.on_conflict_do_update(
                        index_elements=['embedded_track_id', 'sonarrSeriesId', 'sonarrEpisodeId', 'language',
                                        'forced', 'hi'],
                        set_={
                            'language': embedded_stmt.excluded.language,
                            'forced': embedded_stmt.excluded.forced,
                            'hi': embedded_stmt.excluded.hi,
                            'size': embedded_stmt.excluded.size,
                            'embedded_track_id': embedded_stmt.excluded.embedded_track_id
                        },
                        index_where=TableEpisodesSubtitles.path.is_(None)
                    )
                    database.execute(embedded_stmt)

                    # Delete prior indexed embedded subtitles that don't exist anymore
                    embedded_subtitles_id_list = [x['embedded_track_id'] for x in embedded_subtitles]
                    if len(embedded_subtitles_id_list):
                        database.execute(
                            delete(TableEpisodesSubtitles)
                            .where(TableEpisodesSubtitles.sonarrEpisodeId == sonarr_episode_id)
                            .where(TableEpisodesSubtitles.path.is_(None))
                            .where(TableEpisodesSubtitles.embedded_track_id.not_in(embedded_subtitles_id_list))
                        )
            except Exception:
                index_complete = False
                logging.exception(f"BAZARR error when trying to analyze this {os.path.splitext(mapped_path)[1]} file: "
                                  f"{mapped_path}")
                pass

        try:
            dest_folder, full_dest_folder_path, scan_paths = _get_subtitles_scan_paths(mapped_path)
            core.CUSTOM_PATHS = [dest_folder] if dest_folder else []

            # Get previously indexed subtitles that haven't changed:
            previously_indexed_subtitles_to_exclude = []
            previously_indexed_subtitles_to_delete = []
            for subtitles in _get_indexed_subtitles(sonarr_episode_id):
                if not subtitles.path:
                    continue

                mapped_subtitles_path = path_mappings.path_replace(subtitles.path)
                if os.path.isfile(mapped_subtitles_path) and os.stat(mapped_subtitles_path).st_size == subtitles.file_size:
                    previously_indexed_subtitles_to_exclude.append({
                        'path': mapped_subtitles_path,
                        'file_size': subtitles.file_size,
                        'code2': subtitles.code2,
                        'forced': subtitles.forced,
                        'hi': subtitles.hi,
                    })
                elif not os.path.isfile(mapped_subtitles_path):
                    previously_indexed_subtitles_to_delete.append(subtitles.path)

            if previously_indexed_subtitles_to_delete:
                database.execute(
                    delete(TableEpisodesSubtitles)
                    .where(TableEpisodesSubtitles.path.in_(previously_indexed_subtitles_to_delete)))

            # Search for external subtitles:
            subtitles = search_external_subtitles(
                mapped_path,
                languages=languages if languages is not None else get_language_set(),
                only_one=settings.general.single_language,
            )

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

                # We get custom language external subtitles
                try:
                    subtitle_size = os.stat(subtitle_path).st_size
                except FileNotFoundError:
                    logging.debug(f"BAZARR skipping missing subtitle file: {subtitle_path}")
                    continue

                custom = CustomLanguage.found_external(subtitle, subtitle_path)
                if custom is not None:
                    logging.debug(f"BAZARR external subtitles detected: {custom}")
                    external_subtitles.append({'sonarrSeriesId': item.sonarrSeriesId,
                                               'sonarrEpisodeId': sonarr_episode_id,
                                               'language': custom.split(':')[0],
                                               'forced': custom.endswith(':forced'),
                                               'hi': custom.endswith(':hi'),
                                               'path': path_mappings.path_replace_reverse(subtitle_path),
                                               'size': subtitle_size})

                # We get defined and supported language external subtitles
                elif str(language.basename) != 'und':
                    logging.debug(f"BAZARR external subtitles detected: {language}"
                                  f"{':forced' if language.forced else ''}"
                                  f"{':hi' if language.hi else ''}")
                    external_subtitles.append({'sonarrSeriesId': item.sonarrSeriesId,
                                               'sonarrEpisodeId': sonarr_episode_id,
                                               'language': language.basename,
                                               'forced': language.forced,
                                               'hi': language.hi,
                                               'path': path_mappings.path_replace_reverse(subtitle_path),
                                               'size': subtitle_size})

            # We store external subtitles in the database or update existing ones
            if len(external_subtitles):
                stmt = insert(TableEpisodesSubtitles).values(external_subtitles)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['path', 'sonarrSeriesId', 'sonarrEpisodeId', 'language', 'forced', 'hi'],
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
    logging.debug(f"BAZARR has stored those languages to DB: {embedded_subtitles + external_subtitles}")

    updated_episode_values = {
        'missing_subtitles': _get_missing_subtitles_text(
            item.profileId,
            item.audio_language,
            embedded_subtitles + external_subtitles,
        ),
    }
    if index_complete:
        updated_episode_values.update({
            'subtitles_last_indexed_episode_file_id': item.episode_file_id,
            'subtitles_last_indexed_external_signature': _get_subtitles_scan_signature(scan_paths),
            'subtitles_last_indexed_file_size': item.file_size,
            'subtitles_last_indexed_path': item.path,
        })

    database.execute(
        update(TableEpisodes)
        .values(**updated_episode_values)
        .where(TableEpisodes.sonarrEpisodeId == sonarr_episode_id)
    )
    event_stream(type='episode', payload=sonarr_episode_id)
    event_stream(type='episode-wanted', action='update', payload=sonarr_episode_id)
    event_stream(type='badges')

    logging.debug(f'BAZARR ended subtitles indexing for this file: {mapped_path}')


def list_missing_subtitles(no=None, epno=None):
    stmt = select(TableShows.sonarrSeriesId,
                  TableEpisodes.sonarrEpisodeId,
                  TableShows.profileId,
                  TableEpisodes.audio_language) \
        .select_from(TableEpisodes) \
        .join(TableShows)

    if epno is not None:
        episodes_subtitles = database.execute(stmt.where(TableEpisodes.sonarrEpisodeId == epno)).all()
    elif no is not None:
        episodes_subtitles = database.execute(stmt.where(TableEpisodes.sonarrSeriesId == no)).all()
    else:
        episodes_subtitles = database.execute(stmt).all()

    for episode_subtitles in episodes_subtitles:
        missing_subtitles_text = _get_missing_subtitles_text(
            episode_subtitles.profileId,
            episode_subtitles.audio_language,
            [
                {
                    'language': subtitles.code2,
                    'forced': subtitles.forced,
                    'hi': subtitles.hi,
                    'path': subtitles.path,
                }
                for subtitles in _get_indexed_subtitles(episode_subtitles.sonarrEpisodeId)
            ],
        )

        database.execute(
            update(TableEpisodes)
            .values(missing_subtitles=missing_subtitles_text)
            .where(TableEpisodes.sonarrEpisodeId == episode_subtitles.sonarrEpisodeId))

        event_stream(type='episode', payload=episode_subtitles.sonarrEpisodeId)
        event_stream(type='episode-wanted', action='update', payload=episode_subtitles.sonarrEpisodeId)
    event_stream(type='badges')


def series_full_scan_subtitles(job_id=None, use_cache=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Indexing all existing episodes subtitles", is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return

    if use_cache is None:
        use_cache = settings.sonarr.use_ffprobe_cache

    episodes = database.execute(
        select(TableEpisodes.path,
               TableEpisodes.sonarrSeriesId,
               TableEpisodes.episode_file_id,
               TableEpisodes.file_size,
               TableShows.profileId,
               TableEpisodes.audio_language,
               TableEpisodes.missing_subtitles,
               TableEpisodes.subtitles_last_indexed_episode_file_id,
               TableEpisodes.subtitles_last_indexed_external_signature,
               TableEpisodes.subtitles_last_indexed_file_size,
               TableEpisodes.subtitles_last_indexed_path,
               TableShows.title,
               TableEpisodes.title.label("episodeTitle"),
               TableEpisodes.season,
               TableEpisodes.episode,
               TableEpisodes.sonarrEpisodeId,
               select(TableEpisodesSubtitles.id)
               .where(TableEpisodesSubtitles.sonarrEpisodeId == TableEpisodes.sonarrEpisodeId)
               .limit(1)
               .exists()
               .label("has_indexed_subtitles"))
        .select_from(TableEpisodes)
        .join(TableShows)
    ).all()

    jobs_queue.update_job_progress(job_id=job_id, progress_max=len(episodes), progress_message='Indexing')
    scan_signature_cache = {}
    language_set = get_language_set()
    for i, episode in enumerate(episodes, start=1):
        jobs_queue.update_job_progress(
            job_id=job_id, progress_value=i,
            progress_message=f"{episode.title} - S{episode.season:02d}E{episode.episode:02d} - {episode.episodeTitle}")
        mapped_path = path_mappings.path_replace(episode.path)
        if _should_skip_full_scan_episode(episode, mapped_path, scan_signature_cache):
            continue
        store_subtitles(episode.sonarrEpisodeId, use_cache=use_cache, item=episode, languages=language_set)

    logging.info('BAZARR All existing episode subtitles indexed from disk.')

    jobs_queue.update_job_name(job_id=job_id, new_job_name="Indexed all existing series subtitles")

    gc.collect()


def series_scan_subtitles(no):
    language_set = get_language_set()
    episodes = database.execute(
        select(TableEpisodes.sonarrEpisodeId)
        .where(TableEpisodes.sonarrSeriesId == no)
        .order_by(TableEpisodes.sonarrEpisodeId))\
        .all()

    for episode in episodes:
        store_subtitles(episode.sonarrEpisodeId, use_cache=False, languages=language_set)
