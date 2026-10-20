from typing import Protocol

from ..core.translation_request import TranslationRequest, TranslatorConfig


class Translator(Protocol):
    def translate(self, job_id: int | None) -> str | None: ...

class TranslatorFactory:

    @classmethod
    def create_translator(cls, request: TranslationRequest, config: TranslatorConfig) -> Translator:
        if config.name == 'google_translate':
            from .google_translator import GoogleTranslatorService
            return GoogleTranslatorService(request)

        if config.name == 'gemini':
            from .gemini_translator import GeminiTranslatorService
            return GeminiTranslatorService(request, config)

        from .lingarr_translator import LingarrTranslatorService
        return LingarrTranslatorService(request, config)
