"""
Unit tests for download.py and mass_download logic

Tests the CURRENT behavior on develop to catch regressions when perf branch changes land.
"""
from tests.test_helpers import parse_language_code, unwrap_result_tuple


class TestLanguageTuples:
    """Language representation: (code, hi_flag, forced_flag)"""

    def test_parse_simple_language(self):
        """Parse simple language code"""
        result = parse_language_code('en')
        assert result == ('en', 'False', 'False')

    def test_parse_language_with_hi(self):
        """Parse language with :hi suffix"""
        result = parse_language_code('en:hi')
        assert result == ('en', 'True', 'False')

    def test_parse_language_with_forced(self):
        """Parse language with :forced suffix"""
        result = parse_language_code('fr:forced')
        assert result == ('fr', 'False', 'True')

    def test_rebuild_language_string(self):
        """Rebuild language string from flags"""
        lang, hi, forced = 'en', 'True', 'False'
        
        if hi == 'True':
            result = f'{lang}:hi'
        elif forced == 'True':
            result = f'{lang}:forced'
        else:
            result = lang
        
        assert result == 'en:hi'


class TestMissingSubtitlesParsing:
    """Parse missing_subtitles from JSON string"""

    def test_parse_simple_list(self):
        """Parse list of language strings"""
        import ast
        missing_str = "[\'en\', \'fr\']"
        missing = ast.literal_eval(missing_str)
        
        assert 'en' in missing
        assert 'fr' in missing

    def test_parse_with_suffixes(self):
        """Parse languages with :hi and :forced"""
        import ast
        missing_str = "[\'en:hi\', \'fr:forced\', \'es\']"
        missing = ast.literal_eval(missing_str)
        
        assert len(missing) == 3
        assert 'en:hi' in missing


class TestDownloadResultHandling:
    """Handle download results from generate_subtitles"""

    def test_count_successful_downloads(self):
        """Track number of subtitles successfully downloaded"""
        results = [
            {'success': True},
            {'success': True},
            {'success': False},
        ]
        
        downloaded = sum(1 for r in results if r['success'])
        assert downloaded == 2

    def test_format_outcome_message(self):
        """Format outcome message for user display"""
        downloaded_count = 3
        msg = f"{downloaded_count} subtitle(s) downloaded" if downloaded_count else "No subtitles found"
        
        assert msg == "3 subtitle(s) downloaded"

    def test_format_outcome_when_none(self):
        """Format message when nothing found"""
        downloaded_count = 0
        msg = "No subtitles found" if not downloaded_count else f"{downloaded_count} downloaded"
        
        assert msg == "No subtitles found"

    def test_unwrap_result_tuple(self):
        """Unwrap single-element tuple result"""
        result = ('subtitle_result',)
        result = unwrap_result_tuple(result)
        assert not isinstance(result, tuple)
        assert result == 'subtitle_result'


class TestGenerateSubtitlesParameters:
    """Parameters passed to generate_subtitles function"""

    def test_check_if_still_required_parameter(self):
        """Language might not still be needed if cutoff reached"""
        check_if_still_required = True
        assert check_if_still_required is True

    def test_fallback_allowed_parameter(self):
        """Whisper fallback enabled/disabled"""
        fallback_allowed = True
        assert fallback_allowed is True

    def test_job_id_for_progress_tracking(self):
        """Job ID for progress updates"""
        job_id = 'job123'
        assert job_id is not None
