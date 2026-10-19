from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.database import TableEpisodes, TableHistory, TableHistoryMovie, TableMovies, TableShows
from subtitles import upgrade


@pytest.mark.parametrize('media_type', ['series', 'movie'])
@pytest.mark.parametrize('history_ids,expected_ids', [
    (None, {2, 5, 6, 11, 12}), ([], {2, 5, 6, 11, 12}),
    ([3], set()), ([4], set()), ([5], {5}), ([6], {6}),
    ([5, 6, 11, 5], {5, 6, 11}), ([-1], set()),
    ([6] + list(range(1000, 1500)), {6}),
])
def test_upgrade_candidates_filter_in_sql_without_losing_parents_outside_the_page(
    media_type: str, history_ids: list[int] | None, expected_ids: set[int],
    memory_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade, 'settings', SimpleNamespace(general=SimpleNamespace(
        upgrade_subs=True, days_to_upgrade_subs=30, upgrade_manual=True)))
    monkeypatch.setattr(upgrade, 'get_exclusion_clause', lambda kind: [])
    execute = Mock(wraps=memory_session.execute)
    monkeypatch.setattr(upgrade, 'database', SimpleNamespace(execute=execute))
    if media_type == 'series':
        memory_session.add(TableShows(sonarrSeriesId=1, path='/show', title='Show'))
        memory_session.flush()
        memory_session.add(TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1,
                                         path='/show/episode.mkv', season=1, episode=1, title='Episode'))
        model = TableHistory
        identity = {'sonarrEpisodeId': 1, 'sonarrSeriesId': 1}
        load = upgrade.get_upgradable_episode_subtitles
    else:
        memory_session.add(TableMovies(radarrId=1, tmdbId=1, path='/movie.mkv', title='Movie'))
        model = TableHistoryMovie
        identity = {'radarrId': 1}
        load = upgrade.get_upgradable_movies_subtitles
    memory_session.flush()
    # Parent 3 is superseded by 4, which is itself superseded by 5. An expired
    # candidate pointing to 6 must not exclude 6. A deletion excludes 7.
    rows = [
        (1, '/same', 1, 10, 10, None), (2, '/same', 1, 20, 1, 1),
        (3, '/parent', 1, 10, 1, None), (4, '/child', 1, 20, 1, 3),
        (5, '/grandchild', 1, 30, 1, 4), (6, '/other', 1, 10, 1, None),
        (7, '/deleted', 1, 10, 5, None), (8, '/deleted', 0, 10, 4, None),
        (9, '/full-score', 1, 359, 1, None), (10, '/expired', 1, 10, 40, 6),
        (11, '/null-score', 6, None, 1, None), (12, '/manual', 2, 10, 1, None),
    ]
    for history_id, path, action, score, age, parent in rows:
        memory_session.add(model(id=history_id, video_path=path, action=action, score=score,
                                 score_out_of=360, timestamp=datetime.now() - timedelta(days=age),
                                 language='en', description='Downloaded', upgradedFromId=parent, **identity))
        memory_session.flush()
    result = load(history_id_list=history_ids)
    assert result == {key: {2: 1, 5: 4}.get(key) for key in expected_ids}
    execute.assert_called_once()
    statement = execute.call_args.args[0]
    assert len(statement.selected_columns) == 2
    # The same CTE and null-safe parent exclusion must compile on PostgreSQL.
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert 'WITH upgrade_candidates AS' in sql
    assert 'IS NOT NULL' in sql
    if history_ids and len(history_ids) <= 500:
        assert 'upgrade_candidates.id IN' in sql
    else:
        assert 'upgrade_candidates.id IN' not in sql
