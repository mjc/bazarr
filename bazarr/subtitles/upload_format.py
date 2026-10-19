import os

from pysubs2.formats import get_format_identifier
from pysubs2.exceptions import UnknownFileExtensionError
from subliminal_patch.subtitle import Subtitle


def configure_upload_format(subtitle: Subtitle, filename: str) -> None:
    try:
        subtitle.format = get_format_identifier(os.path.splitext(filename)[1])
    except UnknownFileExtensionError:
        return None
