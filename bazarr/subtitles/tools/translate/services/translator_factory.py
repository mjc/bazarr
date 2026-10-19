# coding=utf-8
from typing import Literal, Protocol

from ..core.translation_request import TranslationRequest

TranslatorName = Literal['google_translate', 'gemini', 'lingarr']


class Translator(Protocol):
    def translate(self, job_id: int | None) -> str | None: ...


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
    def create_translator(cls, translator_type: TranslatorName, request: TranslationRequest) -> Translator | None:
        if translator_type == 'google_translate':
            from .google_translator import GoogleTranslatorService
            return GoogleTranslatorService(request)

        elif translator_type == 'gemini':
            from .gemini_translator import GeminiTranslatorService
            return GeminiTranslatorService(request)

        elif translator_type == 'lingarr':
            from .lingarr_translator import LingarrTranslatorService
            return LingarrTranslatorService(request)

        else:
            return None
