# coding=utf-8
from typing import Literal, Protocol

TranslatorName = Literal['google_translate', 'gemini', 'lingarr']


class Translator(Protocol):
    def translate(self, job_id: int | str | None) -> str | None: ...


def resolve_translator_name(value: object) -> TranslatorName | None:
    if value is None or value == '':
        return 'google_translate'
    if value == 'google_translate':
        return 'google_translate'
    if value == 'gemini':
        return 'gemini'
    if value == 'lingarr':
        return 'lingarr'
    return None

class TranslatorFactory:

    @classmethod
    def create_translator(cls, translator_type: TranslatorName, **kwargs) -> Translator | None:
        if translator_type == 'google_translate':
            from .google_translator import GoogleTranslatorService
            return GoogleTranslatorService(**kwargs)

        elif translator_type == 'gemini':
            from .gemini_translator import GeminiTranslatorService
            return GeminiTranslatorService(**kwargs)

        elif translator_type == 'lingarr':
            from .lingarr_translator import LingarrTranslatorService
            return LingarrTranslatorService(**kwargs)

        else:
            return None
