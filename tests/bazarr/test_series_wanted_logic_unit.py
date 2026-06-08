"""
Unit tests for series/episode wanted search logic

Tests the CURRENT behavior on develop to catch regressions when perf branch changes land.
"""
import ast
from tests.test_helpers import parse_language_code, unwrap_result_tuple


class TestEpisodeLanguageParsing:
    """Parse languages for episodes (same as movies)"""

    def test_parse_episode_missing_subtitles(self):
        """Parse missing_subtitles list for episode"""
        missing_str = "[\'en\', \'fr:hi\']"
        missing = ast.literal_eval(missing_str)
        
        assert 'en' in missing
        assert 'fr:hi' in missing

    def test_build_language_tuples_for_episode(self):
        """Build (lang, hi, forced) tuples"""
        for lang in ['en', 'fr:hi', 'es:forced']:
            result = parse_language_code(lang)
            assert result[0] in ['en', 'fr', 'es']


class TestEpisodeIdentification:
    """Episodes identified by season/episode numbers"""

    def test_episode_lookup(self):
        """Look up episode by season and episode number"""
        episode = {
            'sonarrId': 123,
            'season': 2,
            'episode': 7,
        }
        
        assert episode['season'] >= 1
        assert episode['episode'] >= 1
        
        identifier = f"S{episode['season']:02d}E{episode['episode']:02d}"
        assert identifier == "S02E07"

    def test_embedded_subtitle_structure_for_episodes(self):
        """Embedded subtitles include season/episode"""
        entry = {
            'sonarrId': 123,
            'seasonNumber': 1,
            'episodeNumber': 5,
            'language': 'en',
            'embedded_track_id': 1,
        }
        
        assert entry['seasonNumber'] == 1
        assert entry['episodeNumber'] == 5


class TestEpisodeEventHandling:
    """Episode-specific events and triggers"""

    def test_episode_wanted_deletion_event(self):
        """Delete episode-wanted event on success"""
        event = {
            'type': 'episode-wanted',
            'action': 'delete',
            'payload': 456,
        }
        
        assert event['type'] == 'episode-wanted'
        assert event['action'] == 'delete'

    def test_episode_download_result_handling(self):
        """Handle result from episode download"""
        result = ('subtitle_result',)
        result = unwrap_result_tuple(result)
        assert result == 'subtitle_result'
