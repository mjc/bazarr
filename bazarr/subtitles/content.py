"""Keep subtitle content in the byte representation required by saving and mods."""

from subliminal_patch.subtitle import Subtitle


def validate_subtitle(subtitle: Subtitle) -> bool:
    content: bytes | str | None = subtitle.content
    if not content:
        return False
    if isinstance(content, str):
        # Reuse the codec of library-converted text; an unlabelled text payload
        # does not establish the byte encoding required by the vendor contract.
        encoding = subtitle._guessed_encoding or subtitle.encoding
        if not encoding:
            return False
        subtitle.content = content.encode(encoding)
    encoding: str | None = subtitle.get_encoding()
    if encoding is None or not subtitle.is_valid():
        return False
    # MicroDVD validation can replace bytes with converted text. Preserve the
    # encoding selected from the input before the library performs that conversion.
    converted: bytes | str | None = subtitle.content
    if isinstance(converted, str):
        subtitle.content = converted.encode(encoding)
    return bool(subtitle.content)


def prepare_downloaded_subtitle(subtitle: Subtitle) -> None:
    # The pool ignores the hook's return value and validates again afterwards.
    if not validate_subtitle(subtitle):
        subtitle.content = None
        subtitle._is_valid = False
