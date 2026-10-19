from dataclasses import dataclass
from typing import Literal, TypeAlias

from dynaconf.utils.boxing import DynaBox
from languages.registry import ResolvedLanguage


TranslatorName: TypeAlias = Literal['google_translate', 'gemini', 'lingarr']


def resolve_translator_name(value: str | None) -> TranslatorName:
    if value is None or value == '' or value == 'google_translate':
        return 'google_translate'
    if value == 'gemini':
        return 'gemini'
    if value == 'lingarr':
        return 'lingarr'
    raise ValueError(f'Unknown translator type: {value!r}')


@dataclass(frozen=True, slots=True)
class GoogleConfig:
    annotate: bool
    default_score: int
    name: Literal['google_translate'] = 'google_translate'


@dataclass(frozen=True, slots=True)
class GeminiConfig:
    annotate: bool
    default_score: int
    model: str
    keys: tuple[str, ...]
    batch_size: int
    name: Literal['gemini'] = 'gemini'


@dataclass(frozen=True, slots=True)
class LingarrConfig:
    annotate: bool
    default_score: int
    url: str
    token: str
    name: Literal['lingarr'] = 'lingarr'


TranslatorConfig: TypeAlias = GoogleConfig | GeminiConfig | LingarrConfig


def load_translator_config(section: DynaBox) -> TranslatorConfig:
    name = resolve_translator_name(section.translator_type)
    annotate: bool = section.translator_info
    score: int = section.default_score
    if name == 'google_translate':
        return GoogleConfig(annotate, score)
    if name == 'lingarr':
        return LingarrConfig(annotate, score, section.lingarr_url, section.lingarr_token)
    try:
        batch_size = max(1, int(section.gemini_batch_size))
    except (AttributeError, TypeError, ValueError):
        batch_size = 300
    keys: dict[str, None] = {}
    for raw_key in section.gemini_keys:
        key = str(raw_key).strip()
        if key:
            keys[key] = None
    return GeminiConfig(annotate, score, section.gemini_model, tuple(keys), batch_size)


@dataclass(frozen=True, slots=True)
class EpisodeMedia:
    series_id: int
    episode_id: int
    kind: Literal['episode'] = 'episode'


@dataclass(frozen=True, slots=True)
class MovieMedia:
    movie_id: int
    kind: Literal['movie'] = 'movie'


TranslationMedia: TypeAlias = EpisodeMedia | MovieMedia


def resolve_translation_media(media_type: str, series_id: int | None, episode_id: int | None,
                              movie_id: int | None) -> TranslationMedia:
    if media_type == 'episode' and series_id is not None and episode_id is not None:
        return EpisodeMedia(series_id, episode_id)
    if media_type == 'movie' and movie_id is not None:
        return MovieMedia(movie_id)
    raise ValueError('Translation requires an episode or movie identifier')


@dataclass(frozen=True, slots=True)
class TranslationTarget:
    language: ResolvedLanguage
    requested_code2: str
    normalized_code2: str


@dataclass(frozen=True, slots=True)
class TranslationRequest:
    source_srt_file: str
    dest_srt_file: str
    video_path: str
    from_lang: str
    target: TranslationTarget
    media: TranslationMedia
    forced: bool
    hi: bool
