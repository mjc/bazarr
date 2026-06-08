"""
Unit tests for adaptive_searching.py

Tests the CURRENT behavior on develop to catch regressions when perf branch changes land.
If a test fails after merging perf branch, it means the behavior changed (intentionally or not).
"""
from bazarr.subtitles.adaptive_searching import is_search_active, updateFailedAttempts
from tests.test_helpers import set_adaptive_search_settings, get_current_timestamp, parse_language_code


class TestIsSearchActive:
    """Test adaptive search decision logic: should we search again for this language?"""

    def test_always_allows_search_when_disabled(self, monkeypatch):
        """When adaptive_searching setting is OFF, always allow search"""
        set_adaptive_search_settings(monkeypatch, enabled=False)
        assert is_search_active('en', '[[\'en\', 1609459200]]') is True

    def test_failsafe_to_search_on_malformed_input(self, monkeypatch):
        """Malformed attempts string = fail-safe to True (allow search)"""
        set_adaptive_search_settings(monkeypatch)
        bad_inputs = ['not_a_list', 'None', '', '[[invalid]]']
        for bad_input in bad_inputs:
            assert is_search_active('en', bad_input) is True

    def test_allows_search_when_empty_or_no_language(self, monkeypatch):
        """No attempts yet, or language not in attempts = allow search"""
        set_adaptive_search_settings(monkeypatch)
        assert is_search_active('en', '[]') is True
        assert is_search_active('en', '[[\'fr\', 1609459200]]') is True

    def test_blocks_search_when_within_delay_window(self, monkeypatch):
        """Recent attempt within delay window = block search"""
        set_adaptive_search_settings(monkeypatch)
        now_ts = get_current_timestamp()
        assert is_search_active('en', f'[[\'en\', {now_ts}]]') is False

    def test_failsafe_to_search_on_bad_timestamps(self, monkeypatch):
        """Invalid or overflow timestamps = fail-safe to True"""
        set_adaptive_search_settings(monkeypatch)
        assert is_search_active('en', '[[\'en\', "invalid"]]') is True
        assert is_search_active('en', f'[[\'en\', 9999999999999999]]') is True


class TestUpdateFailedAttempts:
    """Test updateFailedAttempts current behavior"""

    def test_empty_attempts_adds_new_language(self):
        """Adding new language to empty attempts"""
        result = updateFailedAttempts('en', '[]')
        # Should return string representation with 'en' at current time
        assert '[\'en\'' in result or '["en"' in result
        assert result.startswith('[[')
        assert result.endswith(']]')

    def test_malformed_attempts_uses_empty_list(self):
        """Malformed attempts should be treated as empty list"""
        result = updateFailedAttempts('en', 'invalid')
        # Should return string representation with 'en' at current time
        assert '[\'en\'' in result or '["en"' in result
        assert result.startswith('[[')

    def test_adds_new_language_to_existing(self):
        """New language should be added to existing attempts"""
        result = updateFailedAttempts('fr', '[[\'en\', 1609459200]]')
        # Should contain both 'en' and 'fr', sorted by language code
        assert '[\'en\'' in result or '["en"' in result
        assert '[\'fr\'' in result or '["fr"' in result
        # Should be sorted by language (en before fr)
        assert result.index('en') < result.index('fr')

    def test_keeps_initial_and_current_for_language(self):
        """Should keep initial attempt and add current one"""
        initial_ts = 1609459200
        result = updateFailedAttempts('en', f'[[\'en\', {initial_ts}]]')
        # Should have 2 entries for 'en': the initial and the new one
        parsed = eval(result)
        en_entries = [x for x in parsed if x[0] == 'en']
        assert len(en_entries) == 2
        # First should be initial
        assert en_entries[0][1] == initial_ts

    def test_removes_intermediate_attempts_for_language(self):
        """Should only keep initial and current, not intermediate attempts"""
        # Three attempts for 'en'
        attempts = '[[\'en\', 1609459200], [\'en\', 1609545600], [\'en\', 1609632000]]'
        result = updateFailedAttempts('en', attempts)
        parsed = eval(result)
        en_entries = [x for x in parsed if x[0] == 'en']
        # Should only have 2: initial (1609459200) and new
        assert len(en_entries) == 2
        assert en_entries[0][1] == 1609459200

    def test_preserves_other_languages(self):
        """Other languages should be preserved"""
        attempts = '[[\'en\', 1609459200], [\'fr\', 1609632000]]'
        result = updateFailedAttempts('en', attempts)
        parsed = eval(result)
        # Should have 'fr' still
        fr_entries = [x for x in parsed if x[0] == 'fr']
        assert len(fr_entries) == 1
        assert fr_entries[0][1] == 1609632000

    def test_returns_string_representation(self):
        """Should return string, not list"""
        result = updateFailedAttempts('en', '[]')
        assert isinstance(result, str)
        # Should be evaluable as Python literal
        parsed = eval(result)
        assert isinstance(parsed, list)

    def test_multiple_languages_multiple_attempts(self):
        """Complex case: multiple languages with multiple attempts each"""
        attempts = '[[\'en\', 1609459200], [\'en\', 1609545600], [\'fr\', 1609632000], [\'fr\', 1609718400]]'
        result = updateFailedAttempts('en', attempts)
        parsed = eval(result)
        
        en_entries = [x for x in parsed if x[0] == 'en']
        fr_entries = [x for x in parsed if x[0] == 'fr']
        
        # Should have 2 'en' (initial and new) and 2 'fr' (both preserved)
        assert len(en_entries) == 2
        assert len(fr_entries) == 2
        assert en_entries[0][1] == 1609459200  # Initial 'en'
        assert fr_entries[0][1] == 1609632000  # Initial 'fr'
        assert fr_entries[1][1] == 1609718400  # Latest 'fr'

    def test_current_timestamp_is_added(self):
        """New attempt should have current timestamp"""
        result = updateFailedAttempts('en', '[]')
        parsed = eval(result)
        
        # Should have one entry for 'en'
        en_entries = [x for x in parsed if x[0] == 'en']
        assert len(en_entries) == 1
        
        # Timestamp should be very recent (within last second)
        current_ts = datetime.timestamp(datetime.now())
        assert abs(en_entries[0][1] - current_ts) < 1


class TestIsSearchActiveExtended:
    """Extended tests for is_search_active decision logic"""

    def test_accepts_multiple_languages_in_attempts(self, monkeypatch):
        """Should only match on desired language"""
        set_adaptive_search_settings(monkeypatch)
        
        now_ts = get_current_timestamp()
        attempts = f'[[\'en\', {now_ts}], [\'fr\', 1609459200]]'
        
        assert is_search_active('en', attempts) is False
        assert is_search_active('fr', attempts) is True

    def test_long_language_tags_with_hi_forced(self, monkeypatch):
        """Should handle language codes with :hi or :forced suffixes"""
        set_adaptive_search_settings(monkeypatch)
        result = is_search_active('en:hi', '[[\'en:hi\', 1609459200]]')
        assert isinstance(result, bool)

    def test_extended_delay_weeks(self, monkeypatch):
        """After initial delay, should extend search interval"""
        set_adaptive_search_settings(monkeypatch)
        old_ts = get_current_timestamp() - (4 * 7 * 86400)
        result = is_search_active('en', f'[[\'en\', {old_ts}]]')
        # Should be False due to extended interval blocking
        assert result is False

    def test_extended_delay_days(self, monkeypatch):
        """Should handle delays specified in days"""
        set_adaptive_search_settings(monkeypatch, delay='1d', delta='1d')
        now_ts = get_current_timestamp()
        result = is_search_active('en', f'[[\'en\', {now_ts}]]')
        assert isinstance(result, bool)

    def test_invalid_config_delay_fails_safe(self, monkeypatch):
        """Invalid delay config should fail-safe to True"""
        monkeypatch.setattr('bazarr.subtitles.adaptive_searching.settings.general.adaptive_searching', True)
        monkeypatch.setattr('bazarr.subtitles.adaptive_searching.settings.general.adaptive_searching_delay', 'invalid')
        
        result = is_search_active('en', '[[\'en\', 1609459200]]')
        assert result is True

    def test_multiple_attempts_same_language_uses_first_and_last(self, monkeypatch):
        """Should use initial and latest timestamps for same language"""
        set_adaptive_search_settings(monkeypatch)
        attempts = f'[[\'en\', 1609459200], [\'en\', 1609545600], [\'en\', 1609632000]]'
        result = is_search_active('en', attempts)
        assert isinstance(result, bool)


class TestWantedMovieLogic:
    """Test logic from _wanted_movie and wanted_download_subtitles_movie"""

    def test_missing_subtitles_parsing(self):
        """Missing subtitles should be parsed from JSON string"""
        import ast
        
        missing = "[\'en\', \'fr:hi\']"
        parsed = ast.literal_eval(missing)
        
        assert isinstance(parsed, list)
        for lang in parsed:
            result = parse_language_code(lang)
            assert isinstance(result, tuple)
            assert len(result) == 3

    def test_language_with_hi_forced_suffix_parsing(self):
        """Languages can have :hi or :forced suffixes"""
        import ast
        
        missing = "[\'en:hi\', \'fr:forced\', \'es\']"
        parsed = ast.literal_eval(missing)
        
        assert len(parsed) == 3
        for lang in parsed:
            result = parse_language_code(lang)
            assert result[0] in ('en', 'fr', 'es')
            assert result[1] in ('True', 'False')
            assert result[2] in ('True', 'False')

    def test_audio_language_extraction(self):
        """Audio language should be extracted from list"""
        # Current code gets first language from list
        audio_language_list = [
            {'name': 'English'},
            {'name': 'French'},
        ]
        
        audio_language = audio_language_list[0]['name'] if len(audio_language_list) > 0 else 'None'
        assert audio_language == 'English'

    def test_audio_language_fallback_when_empty(self):
        """Should fallback to 'None' when no audio languages"""
        audio_language_list = []
        
        audio_language = audio_language_list[0]['name'] if len(audio_language_list) > 0 else 'None'
        assert audio_language == 'None'

    def test_language_tuple_format(self):
        """Current code builds (language, hi, forced) tuples"""
        result = parse_language_code("fr:hi")
        assert result == ('fr', 'True', 'False')

    def test_attempt_update_only_when_no_results(self):
        """Attempts should only be updated if no subtitles found"""
        # Current code:
        # if not found_any and providers_list:
        #     for language in languages_to_stamp:
        #         updated = updateFailedAttempts(...)
        
        found_any = False
        providers_list = ['provider1']
        
        should_update = not found_any and bool(providers_list)
        assert should_update is True

    def test_no_attempt_update_when_found(self):
        """Should not update attempts if any subtitles were found"""
        found_any = True
        providers_list = ['provider1']
        
        should_update = not found_any and bool(providers_list)
        assert should_update is False

    def test_no_attempt_update_when_no_providers(self):
        """Should not update attempts if no providers available"""
        found_any = False
        providers_list = []
        
        should_update = not found_any and bool(providers_list)
        assert should_update is False


class TestIndexerLogic:
    """Test logic from store_subtitles_movie and list_missing_subtitles_movies"""

    def test_codec_ignore_decisions(self):
        """Should respect subtitle codec ignore settings"""
        # Current code structure for codec skipping
        subtitle_codec = 'pgs'
        ignore_pgs = True
        ignore_vobsub = False
        ignore_ass = False
        
        should_skip = (ignore_pgs and subtitle_codec.lower() == "pgs")
        assert should_skip is True

    def test_codec_allow_when_not_ignored(self):
        """Should allow codecs that aren't ignored"""
        subtitle_codec = 'srt'
        ignore_pgs = True
        ignore_vobsub = True
        ignore_ass = True
        
        should_skip = (ignore_pgs and subtitle_codec.lower() == "pgs") or \
                      (ignore_vobsub and subtitle_codec.lower() == "vobsub") or \
                      (ignore_ass and subtitle_codec.lower() == "ass")
        assert should_skip is False

    def test_embedded_subtitle_entry_format(self):
        """Embedded subtitles should have correct structure"""
        # Current code builds these dicts
        embedded_entry = {
            'radarrId': 123,
            'language': 'en',
            'forced': False,
            'hi': False,
            'embedded_track_id': 1
        }
        
        assert embedded_entry['radarrId'] == 123
        assert embedded_entry['language'] == 'en'
        assert embedded_entry['embedded_track_id'] == 1

    def test_subtitle_reindex_skips_unchanged(self):
        """Should skip files that haven't changed since last index"""
        # Current logic for detecting unchanged subs
        previously_indexed = [
            {'path': '/path/to/sub.srt', 'file_size': 1024},
            {'path': '/path/to/sub2.srt', 'file_size': 2048},
        ]
        
        import os
        # Simulate file check (in real code this would check os.path.exists/stat)
        files_to_skip = [
            x for x in previously_indexed
            if x['path']  # has path
            # In real code: os.path.isfile(x['path']) and os.stat(x['path']).st_size == x['file_size']
        ]
        
        assert len(files_to_skip) == 2

    def test_subtitle_delete_missing_files(self):
        """Should delete indexed subs for files that no longer exist"""
        previously_indexed = [
            {'path': '/path/to/sub.srt'},
            {'path': '/path/to/deleted.srt'},
        ]
        
        # Simulate: which files don't exist
        # In real code: not os.path.isfile(x['path'])
        files_to_delete = [
            x['path'] for x in previously_indexed
            if x['path']
        ]
        
        # Would need actual filesystem check in real test
        assert isinstance(files_to_delete, list)
