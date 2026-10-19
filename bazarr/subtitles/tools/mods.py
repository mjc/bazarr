# coding=utf-8

import os
import logging
import stat
import tempfile

from subliminal_patch.subtitle import Subtitle
from subliminal_patch.core import get_subtitle_path
from subzero.language import Language

from app.config import settings
from languages.custom_lang import CustomLanguage
from languages.get_languages import alpha3_from_alpha2
from utilities.subtitle_paths import subtitle_destination


def _write_modified_subtitle(content: bytes, source: str, destination: str) -> None:
    mode = stat.S_IMODE(os.stat(source).st_mode)
    os.makedirs(os.path.dirname(destination) or '.', exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(destination) or '.', prefix='.bazarr-',
                                         delete=False) as output:
            temporary = output.name
            output.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
    if os.path.islink(source) or not os.path.samefile(source, destination):
        os.unlink(source)


def subtitles_apply_mods(language: str, subtitle_path: str, mods: list[str], video_path: str) -> str | None:
    alpha3 = alpha3_from_alpha2(language)
    if alpha3 is None:
        return None
    custom = CustomLanguage.from_value(alpha3, "alpha3")
    if custom is None:
        lang_obj = Language(alpha3)
    else:
        lang_obj = custom.subzero_language()
    single = settings.general.single_language

    sub = Subtitle(lang_obj, mods=mods, original_format=True)
    with open(subtitle_path, 'rb') as f:
        sub.content = f.read()

    if not sub.is_valid():
        logging.exception(f'BAZARR Invalid subtitle file: {subtitle_path}')
        return

    content = sub.get_modified_content(format=sub.format)
    if content:
        if 'remove_HI' in mods:
            # get the modded subtitles path if the subtitles are alongside the video
            modded_subtitles_path_if_alongside_video = get_subtitle_path(
                video_path,
                language=None if single else sub.language,
                forced_tag=sub.language.forced,
                hi_tag=False,
                tags=[],
                extension=f".{sub.format}"
            )

            # get the real modded subtitles path taking into account if the user set up Bazarr to store external
            # subtitles in a custom folder or relative folder
            modded_subtitles_path = subtitle_destination(
                video_path=video_path,
                subtitle=os.path.basename(modded_subtitles_path_if_alongside_video)
            )
        else:
            modded_subtitles_path = subtitle_path

        if modded_subtitles_path is None:
            return None
        _write_modified_subtitle(content, subtitle_path, modded_subtitles_path)
        return modded_subtitles_path
    return None
