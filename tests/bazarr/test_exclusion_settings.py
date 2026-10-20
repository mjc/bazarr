import pytest
from dynaconf.utils.boxing import DynaBox
from sqlalchemy.dialects import sqlite

from app import database as app_database


@pytest.mark.parametrize('media_type', ['series', 'movie'])
@pytest.mark.parametrize('enabled', [False, True])
def test_exclusion_filters_read_one_current_settings_section(
    media_type: str, enabled: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Settings:
        reads = 0
        section = DynaBox({'excluded_tags': ['skip'] if enabled else [],
                           'only_monitored': enabled, 'excluded_series_types': ['anime'] if enabled else [],
                           'exclude_season_zero': enabled})

        @property
        def sonarr(self) -> DynaBox:
            assert media_type == 'series'
            self.reads += 1
            return self.section

        @property
        def radarr(self) -> DynaBox:
            assert media_type == 'movie'
            self.reads += 1
            return self.section

    settings = Settings()
    monkeypatch.setattr(app_database, 'settings', settings)
    clauses = app_database.get_exclusion_clause(media_type)
    shows = app_database.TableShows
    episodes = app_database.TableEpisodes
    movies = app_database.TableMovies
    expected = []
    if enabled:
        if media_type == 'series':
            expected = [~shows.tags.contains("'skip'"), episodes.monitored == 'True',
                        shows.monitored == 'True', shows.seriesType != 'anime', episodes.season != 0]
        else:
            expected = [~movies.tags.contains("'skip'"), movies.monitored == 'True']

    def compile_clauses() -> list[str]:
        return [str(clause.compile(dialect=sqlite.dialect(), compile_kwargs={'literal_binds': True}))
                for clause in clauses]

    assert compile_clauses() == [
        str(clause.compile(dialect=sqlite.dialect(), compile_kwargs={'literal_binds': True}))
        for clause in expected
    ]
    assert settings.reads == 1

    settings.section = DynaBox({'excluded_tags': [], 'only_monitored': False,
                                'excluded_series_types': [], 'exclude_season_zero': False})
    assert app_database.get_exclusion_clause(media_type) == []
    assert settings.reads == 2
