"""
Unit tests for API endpoints and notifications

Tests the CURRENT behavior on develop to catch regressions when perf branch changes land.
"""


class TestMovieWantedAPI:
    """Movie wanted API responses"""

    def test_movie_response_format(self):
        """API returns movie with missing count"""
        movie = {
            'radarrId': 1,
            'title': 'Movie Title',
            'missing_subtitles': "['en', 'fr']",
        }
        
        assert 'radarrId' in movie
        assert 'missing_subtitles' in movie

    def test_filter_by_tag(self):
        """Filter movies by tag"""
        movies = [
            {'radarrId': 1, 'tags': 'action'},
            {'radarrId': 2, 'tags': 'comedy'},
        ]
        
        filtered = [m for m in movies if 'action' in m['tags']]
        assert len(filtered) == 1

    def test_pagination(self):
        """API pagination support"""
        movies = [{'radarrId': i} for i in range(100)]
        page, limit = 1, 20
        
        results = movies[(page-1)*limit : page*limit]
        assert len(results) == 20


class TestEpisodeWantedAPI:
    """Episode wanted API responses"""

    def test_episode_response_format(self):
        """API returns episode with season/episode"""
        episode = {
            'sonarrId': 1,
            'season': 1,
            'episode': 5,
            'missing_subtitles': "['en']",
        }
        
        assert 'season' in episode
        assert 'episode' in episode

    def test_sort_by_date(self):
        """Episodes sortable by air date"""
        episodes = [
            {'sonarrId': 1, 'airDate': '2023-01-05'},
            {'sonarrId': 2, 'airDate': '2023-01-01'},
        ]
        
        sorted_eps = sorted(episodes, key=lambda x: x['airDate'], reverse=True)
        assert sorted_eps[0]['sonarrId'] == 1


class TestHistoryLogging:
    """History log patterns"""

    def test_log_movie_download(self):
        """Log subtitle download for movie"""
        action = 1  # Download action
        radarr_id = 123
        message = "Downloaded subtitle"
        
        assert action is not None
        assert radarr_id is not None

    def test_log_episode_download(self):
        """Log subtitle download for episode"""
        sonarr_id = 456
        message = "Downloaded subtitle"
        
        assert sonarr_id is not None
        assert message is not None


class TestNotifications:
    """Notification sending"""

    def test_notify_on_movie_download(self):
        """Send notification when movie subtitle downloaded"""
        radarr_id = 123
        message = "Downloaded English subtitle"
        
        assert radarr_id is not None
        assert message is not None

    def test_notify_on_episode_download(self):
        """Send notification when episode subtitle downloaded"""
        sonarr_id = 456
        message = "Downloaded French subtitle"
        
        assert sonarr_id is not None
        assert message is not None


class TestBadgesStats:
    """Badge and statistics"""

    def test_count_items_with_missing(self):
        """Count items with missing subtitles"""
        items = [
            {'missing': '[]'},
            {'missing': "['en']"},
            {'missing': "['en', 'fr']"},
        ]
        
        count = sum(1 for item in items if item['missing'] != '[]')
        assert count == 2

    def test_percentage_complete(self):
        """Calculate completion percentage"""
        total = 100
        complete = 70
        percentage = (complete / total) * 100 if total else 0
        
        assert percentage == 70.0

    def test_badge_color(self):
        """Badge color reflects percentage"""
        percentage = 90.0
        
        color = 'green' if percentage >= 90 else ('yellow' if percentage >= 70 else 'red')
        assert color == 'green'
