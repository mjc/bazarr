from typing import TypeAlias

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import class_mapper

from app.database import TableEpisodes, TableMovies, TableShows
from radarr.movie_snapshot import MovieValues
from sonarr.episode_snapshot import EpisodeValues
from sonarr.series_snapshot import SeriesValues


SyncValues: TypeAlias = MovieValues | EpisodeValues | SeriesValues


def values_match(stored: RowMapping | SyncValues, values: SyncValues) -> bool:
    return all(key in stored and stored.get(key) == value for key, value in values.items())


movie_columns = class_mapper(TableMovies).column_attrs
episode_columns = class_mapper(TableEpisodes).column_attrs
series_columns = class_mapper(TableShows).column_attrs
MOVIE_STATE = select(*(movie_columns[name].class_attribute for name in MovieValues.__required_keys__ | {'profileId'}))
EPISODE_STATE = select(*(episode_columns[name].class_attribute for name in EpisodeValues.__required_keys__))
SERIES_STATE = select(*(series_columns[name].class_attribute for name in SeriesValues.__required_keys__ | {'profileId'}))
