# coding=utf-8
from typing import Protocol

from ..core.translation_request import TranslationRequest


class Translator(Protocol):
    def translate(self, job_id: int | None) -> str | None: ...


class TranslatorFactory:

    @classmethod
    def create_translator(cls, translator_type: str, request: TranslationRequest) -> Translator | None:
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
