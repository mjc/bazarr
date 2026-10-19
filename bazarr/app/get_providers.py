# coding=utf-8

import os
import datetime
import logging
import subliminal_patch
import pretty
import time
import traceback
import re

from subzero.language import Language
from subzero.cache_backends.file import SZFileBackend
from subliminal_patch.exceptions import MustGetBlacklisted
from subliminal import region as subliminal_cache_region
from subliminal_patch.extensions import provider_registry

from app.get_args import args
from app.provider_throttling import (ThrottleCounter, ThrottlePolicy, record_failure, reset_failure_counts,
                                     resolve_throttle_policy)
from app.config import settings
from languages.get_languages import CustomLanguage
from app.event_handler import event_stream
from utilities.binaries import get_binary
from radarr.blacklist import blacklist_log_movie
from sonarr.blacklist import blacklist_log
from utilities.analytics import event_tracker

_TRACEBACK_RE = re.compile(r'File "(.*?providers[\\/].*?)", line (\d+)')


PROVIDERS_FORCED_OFF = ["addic7ed", "tvsubtitles", "legendasdivx", "napiprojekt", "shooter",
                        "hosszupuska", "supersubtitles", "titlovi", "assrt"]

throttle_count: dict[str, ThrottleCounter] = {}


def provider_pool():
    if settings.general.multithreading:
        return subliminal_patch.core.SZAsyncProviderPool
    return subliminal_patch.core.SZProviderPool


def _lang_from_str(content: str):
    " Formats: es-MX en@hi es-MX@forced "
    extra_info = content.split("@")
    if len(extra_info) > 1:
        kwargs = {extra_info[-1]: True}
    else:
        kwargs = {}

    content = extra_info[0]

    try:
        code, country = content.split("-")
    except ValueError:
        lang = CustomLanguage.from_value(content)
        if lang is not None:
            lang = lang.subzero_language()
            return lang.rebuild(lang, **kwargs)

        code, country = content, None

    return subliminal_patch.core.Language(code, country, **kwargs)


def get_language_equals(settings_=None):
    settings_ = settings_ or settings

    equals = settings_.general.language_equals
    if not equals:
        return []

    items = []
    for equal in equals:
        try:
            from_, to_ = equal.split(":")
            from_, to_ = _lang_from_str(from_), _lang_from_str(to_)
        except Exception as error:
            logging.info("Invalid equal value: '%s' [%s]", equal, error)
        else:
            items.append((from_, to_))

    return items


def get_providers():
    providers_list = []
    existing_providers = provider_registry.names()
    providers = [x for x in settings.general.enabled_providers if x in existing_providers]
    for provider in providers:
        reason, until, throttle_desc = tp.get(provider, (None, None, None))
        providers_list.append(provider)

        if reason:
            now = datetime.datetime.now()
            if now < until:
                logging.debug("Not using %s until %s, because of: %s", provider,
                              until.strftime("%y/%m/%d %H:%M"), reason)
                providers_list.remove(provider)
            else:
                logging.info("Using %s again after %s, (disabled because: %s)", provider, throttle_desc, reason)
                tp.pop(provider, None)
                set_throttled_providers(str(tp))
        # if forced only is enabled: # fixme: Prepared for forced only implementation to remove providers with don't support forced only subtitles
        #     for provider in providers_list:
        #         if provider in PROVIDERS_FORCED_OFF:
        #             providers_list.remove(provider)

    if not providers_list:
        providers_list = None

    return providers_list


def get_enabled_providers():
    # return enabled provider including those who can be throttled
    if isinstance(settings.general.enabled_providers, list):
        return settings.general.enabled_providers
    else:
        return []


_FFPROBE_BINARY = get_binary("ffprobe")
_FFMPEG_BINARY = get_binary("ffmpeg")


def _get_mediainfo_binary():
    try:
        return get_binary("mediainfo")
    except Exception:
        logging.warning("BAZARR could not find the mediainfo binary; embedded subtitles "
                        "language detection will fall back to ffprobe.")
        return None


def get_providers_auth():
    return {
        'addic7ed': {
            'username': settings.addic7ed.username,
            'password': settings.addic7ed.password,
            'cookies': settings.addic7ed.cookies,
            'user_agent': settings.addic7ed.user_agent,
            'is_vip': settings.addic7ed.vip,
        },
        'avistaz': {
            'cookies': settings.avistaz.cookies,
            'user_agent': settings.avistaz.user_agent,
        },
        'cinemaz': {
            'cookies': settings.cinemaz.cookies,
            'user_agent': settings.cinemaz.user_agent,
        },
        'opensubtitlescom': {'username': settings.opensubtitlescom.username,
                             'password': settings.opensubtitlescom.password,
                             'use_hash': settings.opensubtitlescom.use_hash,
                             'include_ai_translated': settings.opensubtitlescom.include_ai_translated,
                             'include_machine_translated': settings.opensubtitlescom.include_machine_translated,
                             'api_key': 's38zmzVlW7IlYruWi7mHwDYl2SfMQoC1'
                             },
        'napiprojekt': {'hash_only': settings.napiprojekt.hash_only,
                        'only_authors': settings.napiprojekt.only_authors,
                        'only_real_names': settings.napiprojekt.only_real_names},
        'legendasdivx': {
            'username': settings.legendasdivx.username,
            'password': settings.legendasdivx.password,
            'skip_wrong_fps': settings.legendasdivx.skip_wrong_fps,
        },
        'legendasnet': {
            'username': settings.legendasnet.username,
            'password': settings.legendasnet.password,
        },
        'pipocas': {
            'username': settings.pipocas.username,
            'password': settings.pipocas.password,
        },
        'xsubs': {
            'username': settings.xsubs.username,
            'password': settings.xsubs.password,
        },
        'assrt': {
            'token': settings.assrt.token,
        },
        'napisy24': {
            'username': settings.napisy24.username,
            'password': settings.napisy24.password,
        },
        'betaseries': {'token': settings.betaseries.token},
        'titulky': {
            'username': settings.titulky.username,
            'password': settings.titulky.password,
            'approved_only': settings.titulky.approved_only,
            'skip_wrong_fps': settings.titulky.skip_wrong_fps,
        },
        'titlovi': {
            'username': settings.titlovi.username,
            'password': settings.titlovi.password,
        },
        'jimaku': {
            'api_key': settings.jimaku.api_key,
            'enable_name_search_fallback': settings.jimaku.enable_name_search_fallback,
            'enable_archives_download': settings.jimaku.enable_archives_download,
            'enable_ai_subs': settings.jimaku.enable_ai_subs,
        },
        'ktuvit': {
            'email': settings.ktuvit.email,
            'hashed_password': settings.ktuvit.hashed_password,
        },
        'embeddedsubtitles': {
            'included_codecs': settings.embeddedsubtitles.included_codecs,
            'hi_fallback': settings.embeddedsubtitles.hi_fallback,
            'cache_dir': os.path.join(args.config_dir, "cache"),
            'ffprobe_path': _FFPROBE_BINARY,
            'ffmpeg_path': _FFMPEG_BINARY,
            'timeout': settings.embeddedsubtitles.timeout,
            'unknown_as_fallback': settings.embeddedsubtitles.unknown_as_fallback,
            'fallback_lang': settings.embeddedsubtitles.fallback_lang,
            'mediainfo_path': _get_mediainfo_binary() if settings.embeddedsubtitles.use_mediainfo else None,
        },
        'karagarga': {
            'username': settings.karagarga.username,
            'password': settings.karagarga.password,
            'f_username': settings.karagarga.f_username,
            'f_password': settings.karagarga.f_password,
        },
        'hdbits': {
            'username': settings.hdbits.username,
            'passkey': settings.hdbits.passkey,
        },
        'subf2m': {
            'verify_ssl': settings.subf2m.verify_ssl,
            'user_agent': settings.subf2m.user_agent,
        },
        'whisperai': {
            'endpoint': settings.whisperai.endpoint,
            'response': settings.whisperai.response,
            'timeout': settings.whisperai.timeout,
            'ffmpeg_path': _FFMPEG_BINARY,
            'ffprobe_path': _FFPROBE_BINARY,
            'loglevel': settings.whisperai.loglevel,
            'pass_video_name': settings.whisperai.pass_video_name,
        },
        "animetosho": {
            'search_threshold': settings.animetosho.search_threshold,
        },
        "animetosho_xyz": {},
        "tsukihime": {},
        "subdl": {
            'api_key': settings.subdl.api_key,
            'ai_translate': settings.subdl.ai_translate,
            'include_ai_translated': settings.subdl.include_ai_translated,
        },
        'turkcealtyaziorg': {
            'cookies': settings.turkcealtyaziorg.cookies,
            'user_agent': settings.turkcealtyaziorg.user_agent,
        },
        'subsource': {
            'api_key': settings.subsource.apikey,
        },
        'subsarr': {
            'base_url': settings.subsarr.base_url,
        },
        'subsdump': {
            'base_url': settings.subsdump.base_url,
            'api_key': settings.subsdump.api_key,
        },
        'animesubinfo': {},
        'subx':
            {
                'api_key': settings.subx.api_key,
            },
        'subsro': {
            'api_key': settings.subsro.api_key,
        },
        'subtitlecat': {
            'include_machine_translated': settings.subtitlecat.include_machine_translated,
        },
    }


# download.py doesn't have an exception with the id and media type on it, so can't take an exception 
# as _handle_mgb() does. 
def blacklist_subtitle(name, subs_id, media_type, ids, language):
    if language.forced:
        language_str = f'{language.basename}:forced'
    elif language.hi:
        language_str = f'{language.basename}:hi'
    else:
        language_str = language.basename

    if ids:
        if media_type == "series":
            if ids.get('sonarrSeriesId') and ids.get('sonarrEpisodeId'):
                blacklist_log(ids['sonarrSeriesId'], ids['sonarrEpisodeId'], name, subs_id, language_str)
            else:
                logging.debug(f'BAZARR cannot blacklist subtitle {subs_id} from {name}: unknown series or '
                              f'episode id')
        elif ids.get('radarrId'):
            blacklist_log_movie(ids['radarrId'], name, subs_id, language_str)
        else:
            logging.debug(f'BAZARR cannot blacklist subtitle {subs_id} from {name}: unknown id')


def _handle_mgb(name, exception, ids, language):
    blacklist_subtitle(name, exception.id, exception.media_type, ids, language)


def provider_throttle(name: str, exception: Exception, ids: dict[str, int | None] | None = None,
                      language: Language | None = None) -> None:
    if isinstance(exception, MustGetBlacklisted) and ids is not None and language is not None:
        _handle_mgb(name, exception, ids, language)
        return

    if type(exception) is ValueError and exception.args and isinstance(exception.args[0], str) and \
            exception.args[0].startswith('unsupported pickle protocol'):
        backend = subliminal_cache_region.backend
        if isinstance(backend, SZFileBackend):
            for filename in backend.all_filenames:
                try:
                    os.remove(filename)
                except OSError:
                    logging.debug("Couldn't remove cache file: %s", os.path.basename(filename))
        update_throttled_provider()
        return

    policy = resolve_throttle_policy(name, exception)
    if policy.failures == 1 or throttled_count(name, policy):
        cls_name = type(exception).__name__
        throttle_until, throttle_description = policy.deadline(datetime.datetime.now())
        tp[name] = (policy.reset_reason or cls_name, throttle_until, throttle_description)
        set_throttled_providers(str(tp))

        trac_info = _get_traceback_info(exception)
        logging.info("Throttling %s for %s, until %s, because of: %s. Exception info: %r", name,
                     throttle_description, throttle_until.strftime("%y/%m/%d %H:%M"), cls_name, trac_info)
        event_tracker.track_throttling(provider=name, exception_name=cls_name, exception_info=trac_info)

    update_throttled_provider()


def _get_traceback_info(exc: Exception) -> str:
    traceback_str = " ".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    clean_msg = str(exc).replace("\n", " ").strip()

    line_info = _TRACEBACK_RE.findall(traceback_str)

    # Value info max chars len is 100

    if not line_info:
        return clean_msg[:100]

    line_info = line_info[-1]
    file_, line = line_info

    extra = f"' ~ {os.path.basename(file_)}@{line}"[:90]
    message = f"'{clean_msg}"[:100 - len(extra)]

    return message + extra


def throttled_count(name: str, policy: ThrottlePolicy = ThrottlePolicy(
    datetime.timedelta(minutes=10), '10 minutes', failures=5,
)) -> bool:
    count = record_failure(throttle_count, name, policy)
    if count >= policy.failures:
        return True
    logging.info("Provider %s throttle count %s of %s, waiting 5sec and trying again", name, count, policy.failures)
    time.sleep(5)
    return False


def update_throttled_provider():
    existing_providers = provider_registry.names()
    providers_list = [x for x in settings.general.enabled_providers if x in existing_providers]

    for provider in list(tp):
        if provider not in providers_list:
            tp.pop(provider, None)
            set_throttled_providers(str(tp))

        reason, until, throttle_desc = tp.get(provider, (None, None, None))

        if reason:
            now = datetime.datetime.now()
            if now < until:
                pass
            else:
                logging.info("Using %s again after %s, (disabled because: %s)", provider, throttle_desc, reason)
                tp.pop(provider, None)
                set_throttled_providers(str(tp))

            reason, until, throttle_desc = tp.get(provider, (None, None, None))

            if reason:
                now = datetime.datetime.now()
                if now >= until:
                    logging.info("Using %s again after %s, (disabled because: %s)", provider, throttle_desc, reason)
                    tp.pop(provider, None)
                    set_throttled_providers(str(tp))

    event_stream(type='badges')


def list_throttled_providers():
    update_throttled_provider()
    throttled_providers = []
    existing_providers = provider_registry.names()
    providers = [x for x in settings.general.enabled_providers if x in existing_providers]
    for provider in providers:
        reason, until, throttle_desc = tp.get(provider, (None, None, None))
        throttled_providers.append([provider, reason, pretty.date(until)])
    return throttled_providers


def reset_throttled_providers(only_auth_or_conf_error: bool = False) -> None:
    removed = []
    for provider in list(tp):
        if only_auth_or_conf_error and tp[provider][0] not in ['AuthenticationError', 'ConfigurationError',
                                                               'PaymentRequired']:
            continue
        tp.pop(provider, None)
        removed.append(provider)
    reset_failure_counts(throttle_count, removed if only_auth_or_conf_error else None)
    set_throttled_providers(str(tp))
    update_throttled_provider()
    if only_auth_or_conf_error:
        logging.info('BAZARR throttled providers have been reset (only AuthenticationError, ConfigurationError and '
                     'PaymentRequired).')
    else:
        logging.info('BAZARR throttled providers have been reset.')


def get_throttled_providers():
    providers = {}
    try:
        if os.path.exists(os.path.join(args.config_dir, 'config', 'throttled_providers.dat')):
            with open(os.path.normpath(os.path.join(args.config_dir, 'config', 'throttled_providers.dat')), 'r') as \
                    handle:
                providers = eval(handle.read())
    except Exception:
        # set empty content in throttled_providers.dat
        logging.error("Invalid content in throttled_providers.dat. Resetting")
        set_throttled_providers(str(providers))
    return providers


def set_throttled_providers(data):
    with open(os.path.normpath(os.path.join(args.config_dir, 'config', 'throttled_providers.dat')), 'w+') as handle:
        handle.write(data)


tp = get_throttled_providers()
if not isinstance(tp, dict):
    raise ValueError('tp should be a dict')
