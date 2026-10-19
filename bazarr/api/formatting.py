# coding=utf-8

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import pretty

from api.response_types import (AudioValue, BlacklistResponse, BlacklistRow, EpisodeResponse, EpisodeRow,
                                HistoryResponse, HistoryRow, LanguageValue, MovieResponse, MovieRow,
                                SeriesResponse, SeriesRow, WantedResponse, WantedRow)
from languages.formatting import LanguageInfo, SubtitleLanguage, format_audio_languages, subtitle_language
from utilities.subtitle_metadata import SubtitleFile
from utilities.text_list import parse_string_list, parse_text_list, parse_text_tuple


PathTranslator = Callable[[str | None], str | None]


@dataclass(frozen=True)
class ResponseFormatter:
    show_desired_subtitles: bool
    undefined_audio_language: str | None
    base_url: str
    series_path: PathTranslator
    movie_path: PathTranslator
    desired_languages: Mapping[int, frozenset[str]] = field(default_factory=dict)
    _text_lists: dict[str, tuple[str | None, ...]] = field(default_factory=dict, init=False)
    _audio_languages: dict[tuple[str | None, ...], list[LanguageInfo]] = field(default_factory=dict, init=False)
    _subtitle_languages: dict[tuple[str, bool, bool], SubtitleLanguage] = field(default_factory=dict, init=False)

    def _parse_list(self, value: str) -> tuple[str | None, ...]:
        if value not in self._text_lists:
            self._text_lists[value] = parse_text_tuple(value)
        return self._text_lists[value]

    def audio_names(self, value: str) -> tuple[str | None, ...]:
        try:
            return self._parse_list(value)
        except ValueError:
            return ()

    def _audio(self, value: str | None, names: tuple[str | None, ...] | None = None) -> AudioValue:
        if value is None:
            return None
        if not value:
            return ''
        if names is None:
            names = self.audio_names(value)
        if names not in self._audio_languages:
            self._audio_languages[names] = format_audio_languages(names, self.undefined_audio_language)
        return [language.copy() for language in self._audio_languages[names]]

    def _tags(self, value: str | None) -> list[str | None]:
        return list(self._parse_list(value)) if value is not None else []

    def _image(self, value: str | None, media_type: str) -> str | None:
        return f'{self.base_url}/images/{media_type}{value}' if value else None

    def _subtitles(self, subtitles: Sequence[SubtitleFile], profile_id: int | None) -> list[SubtitleFile]:
        # Database subtitle loaders supply name/forced order; filtering preserves it.
        if not self.show_desired_subtitles:
            return [subtitle.copy() for subtitle in subtitles]
        codes = self.desired_languages[profile_id] if profile_id else frozenset()
        return [subtitle.copy() for subtitle in subtitles if subtitle['code2'] in codes or subtitle['path']]

    def series(self, row: SeriesRow, *, episode_audio: tuple[str | None, ...] | None) -> SeriesResponse:
        if row['audio_language'] == '[]' and episode_audio is None:
            raise ValueError('Series episode audio must be loaded before formatting')
        return {
            **row,
            'alternativeTitles': parse_text_list(row['alternativeTitles']) if row['alternativeTitles'] else [],
            'audio_language': self._audio(row['audio_language'], episode_audio if row['audio_language'] == '[]' else None),
            'tags': self._tags(row['tags']),
            'monitored': row['monitored'] == 'True',
            'path': self.series_path(row.get('path')),
            'poster': self._image(row.get('poster'), 'series'),
            'fanart': self._image(row.get('fanart'), 'series'),
        }

    def episode(self, row: EpisodeRow, *, subtitles: Sequence[SubtitleFile]) -> EpisodeResponse:
        return {
            **row,
            'audio_language': self._audio(row['audio_language']),
            'missing_subtitles': self.missing_languages(row['missing_subtitles']),
            'monitored': row['monitored'] == 'True',
            'path': self.series_path(row.get('path')),
            'subtitles': self._subtitles(subtitles, row.get('profileId')),
        }

    def movie(self, row: MovieRow, *, subtitles: Sequence[SubtitleFile]) -> MovieResponse:
        return {
            **row,
            'alternativeTitles': parse_text_list(row['alternativeTitles']) if row['alternativeTitles'] else [],
            'audio_language': self._audio(row['audio_language']),
            'tags': self._tags(row['tags']),
            'missing_subtitles': self.missing_languages(row['missing_subtitles']),
            'monitored': row['monitored'] == 'True',
            'path': self.movie_path(row.get('path')),
            'poster': self._image(row.get('poster'), 'movies'),
            'fanart': self._image(row.get('fanart'), 'movies'),
            'subtitles': self._subtitles(subtitles, row.get('profileId')),
        }

    def wanted(self, row: WantedRow) -> WantedResponse:
        return {**row, 'tags': self._tags(row['tags']),
                'missing_subtitles': self.missing_languages(row['missing_subtitles'])}

    def blacklist(self, row: BlacklistRow) -> BlacklistResponse:
        return {**row, 'language': self.language(row['language']),
                'timestamp': pretty.date(row['timestamp']), 'parsed_timestamp': row['timestamp'].strftime('%x %X')}

    def episode_history(self, row: HistoryRow, *, can_upgrade: bool) -> HistoryResponse:
        result = self._history(row, can_upgrade=can_upgrade, replace_path=self.series_path)
        result['seriesTitle'] = row.get('seriesTitle')
        result['episode_number'] = row.get('episode_number')
        result['episodeTitle'] = row.get('episodeTitle')
        result['sonarrSeriesId'] = row.get('sonarrSeriesId')
        result['sonarrEpisodeId'] = row.get('sonarrEpisodeId')
        return result

    def movie_history(self, row: HistoryRow, *, can_upgrade: bool) -> HistoryResponse:
        result = self._history(row, can_upgrade=can_upgrade, replace_path=self.movie_path)
        result['title'] = row.get('title')
        result['radarrId'] = row.get('radarrId')
        return result

    def _history(self, row: HistoryRow, *, can_upgrade: bool, replace_path: PathTranslator) -> HistoryResponse:
        timestamp = row['timestamp']
        score, maximum = row['score'], row['score_out_of']
        if score:
            if maximum is None:
                raise TypeError('History score requires score_out_of')
            formatted_score = f'{round(int(score) * 100 / maximum, 2)}%'
        else:
            formatted_score = str(score) if score is not None else None
        upgradable = can_upgrade and (
            replace_path(row.get('subtitles_path')) == replace_path(row['external_subtitles'])
            and replace_path(row['video_path']) == replace_path(row.get('path'))
        )
        return {
            'action': row.get('action'), 'description': row.get('description'),
            'subs_id': row.get('subs_id'), 'provider': row.get('provider'),
            'blacklisted': row.get('blacklisted', False),
            'tags': self._tags(row['tags']), 'monitored': row['monitored'] == 'True',
            'language': self.language(row['language']), 'score': formatted_score,
            'timestamp': pretty.date(timestamp) if timestamp else None,
            'parsed_timestamp': timestamp.strftime('%x %X') if timestamp else None,
            'matches': parse_string_list(row['matched'] or '[]'),
            'dont_matches': parse_string_list(row['not_matched'] or '[]'),
            'subtitles_path': replace_path(row.get('subtitles_path')), 'upgradable': upgradable,
        }

    def _subtitle_language(self, code: str, forced: bool, hi: bool) -> SubtitleLanguage:
        key = code, forced, hi
        if key not in self._subtitle_languages:
            self._subtitle_languages[key] = subtitle_language(code, forced=forced, hi=hi)
        return self._subtitle_languages[key].copy()

    def language(self, value: str | None) -> LanguageValue:
        if value is None or value == 'None':
            return None
        if not value:
            return ''
        return self._subtitle_language(value.split(':')[0], forced=value.endswith(':forced'), hi=value.endswith(':hi'))

    def missing_languages(self, values: Sequence[str]) -> list[SubtitleLanguage]:
        languages: list[SubtitleLanguage] = []
        for value in values:
            parts = value.split(':')
            languages.append(self._subtitle_language(parts[0], forced=len(parts) > 1 and parts[1] == 'forced',
                                                      hi=len(parts) > 1 and parts[1] == 'hi'))
        return languages
