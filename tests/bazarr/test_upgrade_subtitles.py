from tests.helpers.profile_records import profile_record, profile_catalog
# -*- coding: utf-8 -*-
"""
Tests for subtitle upgrade helpers and the minimum score rule in subtitles/upgrade.py.

The minimum score passed to the provider search is max(previous score, minimum_score
percentage of score_out_of). Commit 4fbed3043 replaced a hardcoded previous score + 1.
"""
from types import SimpleNamespace
from unittest import mock
from datetime import datetime
from weakref import ref

import pytest
from subliminal_patch.score import MAX_SCORES

from constants import HI_EXCLUDED
from app.database import (TableLanguagesProfiles, TableShows, TableEpisodes, TableHistory, TableHistoryMovie,
                          TableMovies, TableEpisodesSubtitles, TableMoviesSubtitles)
from subtitles import upgrade


class _Section(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


# parse_language_string

@pytest.mark.parametrize(
    "language_string, expected",
    [
        ("en", ["en", "False", "False"]),
        ("en:hi", ["en", "False", "True"]),
        ("en:forced", ["en", "True", "False"]),
        ("pt-BR", ["pt-BR", "False", "False"]),
    ],
)
def test_parse_language_string(language_string, expected):
    assert upgrade.parse_language_string(language_string) == expected


@pytest.mark.parametrize('item,expected', [
    ({'language': 'en', 'forced': 'True', 'hi': 'False'}, {'en:forced'}),
    ({'language': 'fr', 'forced': 'False', 'hi': 'True'}, {'fr:hi'}),
    ({'language': 'de', 'forced': 'False', 'hi': HI_EXCLUDED}, {'de'}),
    ({'language': 'es', 'forced': 'False', 'hi': 'False'}, {'es', 'es:hi'}),
])
def test_profile_precomputes_upgrade_language_tokens(item, expected):
    assert profile_record({'items': [item]}).upgrade_languages == expected


PROFILE = profile_record({'profileId': 1, 'items': [
    {'language': 'en', 'hi': 'True'}, {'language': 'fr', 'hi': 'False'},
]})


def test_profile_memberships_preserve_upgrade_and_hi_rules():
    assert 'fr' in PROFILE.upgrade_languages
    assert 'de' not in PROFILE.upgrade_languages
    assert 'en' in PROFILE.hi_required_languages
    assert 'fr' not in PROFILE.hi_required_languages


# Minimum score in the upgrade jobs

MINIMUM_SCORE = 90


@pytest.fixture
def upgrade_settings():
    general = _Section(
        minimum_score=MINIMUM_SCORE,
        minimum_score_movie=MINIMUM_SCORE,
        upgrade_subs=True,
    )
    with mock.patch.object(upgrade, "settings", SimpleNamespace(general=general)):
        yield general


@pytest.fixture
def job_environment():
    """Patch every side effect of the upgrade jobs and expose the generate_subtitles mock."""
    job = mock.MagicMock()
    job.cancel_event.is_set.return_value = False
    jobs_queue = mock.MagicMock()
    jobs_queue.get_job.return_value = job

    patches = {
        "jobs_queue": mock.patch.object(upgrade, "jobs_queue", jobs_queue),
        "providers": mock.patch.object(upgrade, "get_providers", return_value=["opensubtitles"]),
        "audio": mock.patch.object(upgrade, "get_audio_profile_languages", return_value=[]),
        "profiles": mock.patch.object(upgrade, "load_profile_catalog", return_value=profile_catalog(PROFILE)),
        "generate": mock.patch.object(upgrade, "generate_subtitles", return_value=[]),
        "store": mock.patch.object(upgrade, "store_subtitles"),
        "store_movie": mock.patch.object(upgrade, "store_subtitles_movie"),
        "history": mock.patch.object(upgrade, "history_log"),
        "history_movie": mock.patch.object(upgrade, "history_log_movie"),
        "notify": mock.patch.object(upgrade, "send_notifications"),
        "notify_movie": mock.patch.object(upgrade, "send_notifications_movie"),
        "event": mock.patch.object(upgrade, "event_stream"),
        "mappings": mock.patch.object(upgrade, "path_mappings", mock.MagicMock(**{
            "path_replace.side_effect": lambda p: p,
            "path_replace_movie.side_effect": lambda p: p,
        })),
    }
    started = {name: p.start() for name, p in patches.items()}
    try:
        yield SimpleNamespace(generate=started["generate"], jobs_queue=jobs_queue)
    finally:
        for p in patches.values():
            p.stop()


def _episode_row(**overrides):
    row = dict(
        id=10, seriesTitle="Show", season=1, episode=2, title="Pilot", language="en:hi",
        audio_language="", video_path="/tv/show/ep.mkv", sceneName="ep", score=50,
        score_out_of=120, sonarrEpisodeId=5, sonarrSeriesId=3, subtitles_path="/tv/show/ep.en.srt",
        path="/tv/show/ep.mkv", profileId=1, external_subtitles="/tv/show/ep.en.srt",
    )
    row.update(overrides)
    return SimpleNamespace(**row)


def _movie_row(**overrides):
    row = dict(
        id=20, title="Movie", language="en:hi", audio_language="", video_path="/movies/m.mkv",
        sceneName="m", score=50, score_out_of=120, radarrId=7, path="/movies/m.mkv", profileId=1,
        subtitles_path="/movies/m.en.srt", external_subtitles="/movies/m.en.srt",
    )
    row.update(overrides)
    return SimpleNamespace(**row)


def _run_job(rows, runner, upgradable_ids):
    """Run an upgrade job against the given rows, returning the forced_minimum_score it used."""
    with mock.patch.object(upgrade, "database") as database, \
            mock.patch.object(upgrade, "get_upgradable_episode_subtitles", return_value=upgradable_ids), \
            mock.patch.object(upgrade, "get_upgradable_movies_subtitles", return_value=upgradable_ids):
        database.execute.return_value.__iter__.return_value = iter(rows)
        runner(job_id="job-1")


def _used_minimum_score(environment):
    environment.generate.assert_called_once()
    return environment.generate.call_args.kwargs["forced_minimum_score"]


def test_episode_minimum_score_uses_out_of_when_higher(upgrade_settings, job_environment):
    _run_job([_episode_row(score=50, score_out_of=120)], upgrade.upgrade_episodes_subtitles, {10: None})

    assert _used_minimum_score(job_environment) == round(MINIMUM_SCORE * 120 / 100)


def test_episode_minimum_score_keeps_previous_score_when_higher(upgrade_settings, job_environment):
    _run_job([_episode_row(score=200, score_out_of=120)], upgrade.upgrade_episodes_subtitles, {10: None})

    assert _used_minimum_score(job_environment) == 200


def test_episode_minimum_score_falls_back_to_max_scores_without_out_of(upgrade_settings, job_environment):
    _run_job([_episode_row(score=1, score_out_of=None)], upgrade.upgrade_episodes_subtitles, {10: None})

    assert _used_minimum_score(job_environment) == round(MINIMUM_SCORE * MAX_SCORES["episode"] / 100)


def test_movie_minimum_score_uses_out_of_when_higher(upgrade_settings, job_environment):
    _run_job([_movie_row(score=50, score_out_of=120)], upgrade.upgrade_movies_subtitles, {20: None})

    assert _used_minimum_score(job_environment) == round(MINIMUM_SCORE * 120 / 100)


def test_movie_minimum_score_falls_back_to_max_scores_without_out_of(upgrade_settings, job_environment):
    _run_job([_movie_row(score=1, score_out_of=None)], upgrade.upgrade_movies_subtitles, {20: None})

    assert _used_minimum_score(job_environment) == round(MINIMUM_SCORE * MAX_SCORES["movie"] / 100)


def test_episode_job_skips_subtitles_no_longer_upgradable(upgrade_settings, job_environment):
    _run_job([_episode_row()], upgrade.upgrade_episodes_subtitles, {})

    job_environment.generate.assert_not_called()


def test_episode_job_skips_subtitles_missing_from_disk(upgrade_settings, job_environment, memory_session, monkeypatch):
    memory_session.add(TableLanguagesProfiles(profileId=1, name='Profile', items='[]'))
    memory_session.flush()
    memory_session.add(TableShows(sonarrSeriesId=3, title='Show', path='/tv/show', profileId=1))
    memory_session.flush()
    memory_session.add(TableEpisodes(sonarrEpisodeId=5, sonarrSeriesId=3, title='Pilot', season=1, episode=2,
                                     path='/tv/show/ep.mkv'))
    memory_session.flush()
    memory_session.add(TableHistory(id=10, sonarrSeriesId=3, sonarrEpisodeId=5, action=1, description='Downloaded',
                                   language='en:hi', video_path='/tv/show/ep.mkv', subtitles_path='/tv/show/ep.en.srt'))
    memory_session.flush()
    monkeypatch.setattr(upgrade, 'database', memory_session)
    monkeypatch.setattr(upgrade, 'get_upgradable_episode_subtitles', lambda: {10: None})

    upgrade.upgrade_episodes_subtitles(job_id='job-1')

    job_environment.generate.assert_not_called()


@pytest.mark.parametrize('media_type', ['series', 'movie'])
@pytest.mark.parametrize('outcome', ['success', 'cancelled', 'profile_removed'])
def test_upgrade_jobs_load_only_candidates_with_current_video_and_subtitle_paths(
    upgrade_settings, job_environment, memory_session, monkeypatch, media_type, outcome,
):
    memory_session.add(TableLanguagesProfiles(profileId=1, name='Profile', items='[]'))
    memory_session.flush()
    for media_id in range(1, 9):
        profile_id = None if media_id == 4 else 1
        if media_type == 'series':
            memory_session.add(TableShows(sonarrSeriesId=media_id, title='Show', path=f'/show/{media_id}',
                                           profileId=profile_id))
            memory_session.flush()
            memory_session.add(TableEpisodes(sonarrEpisodeId=media_id, sonarrSeriesId=media_id, title='Episode',
                                             season=1, episode=media_id, path=f'/video/{media_id}.mkv'))
            history_model, subtitle_model = TableHistory, TableEpisodesSubtitles
            identity = {'sonarrEpisodeId': media_id, 'sonarrSeriesId': media_id}
        else:
            memory_session.add(TableMovies(radarrId=media_id, tmdbId=str(media_id), title='Movie',
                                           path=f'/video/{media_id}.mkv', profileId=profile_id))
            history_model, subtitle_model = TableHistoryMovie, TableMoviesSubtitles
            identity = {'radarrId': media_id}
        memory_session.flush()
        memory_session.add(history_model(
            id=100 + media_id, action=1, description='Downloaded', score=50, score_out_of=120,
            video_path='/previous/video.mkv' if media_id == 5 else f'/video/{media_id}.mkv',
            subtitles_path=None if media_id == 7 else f'/subtitles/{media_id}.srt',
            language='fr:hi' if media_id == 2 else 'de' if media_id == 3 else 'en:hi',
            timestamp=datetime.now(), **identity))
        memory_session.add(subtitle_model(
            path=None if media_id == 7 else '/different/subtitle.srt' if media_id == 6 else f'/subtitles/{media_id}.srt',
            language='en', hi=False, forced=False, **identity))
    memory_session.flush()
    # Other indexed metadata can share a path, and every media file can have
    # many sidecars. Neither case should multiply the history row returned.
    identity = {'sonarrEpisodeId': 1, 'sonarrSeriesId': 1} if media_type == 'series' else {'radarrId': 1}
    memory_session.add(subtitle_model(path='/subtitles/1.srt', language='fr', hi=True, forced=False, **identity))
    for index in range(50):
        memory_session.add(subtitle_model(path=f'/other/{index}.srt', language='en', hi=False, forced=False, **identity))
    memory_session.flush()
    candidates = {history_id: 77 if history_id == 101 else None for history_id in range(101, 108)}
    loader = 'get_upgradable_episode_subtitles' if media_type == 'series' else 'get_upgradable_movies_subtitles'
    monkeypatch.setattr(upgrade, loader, lambda: candidates)
    statements, hydrated_ids = [], []

    def execute(statement):
        statements.append(statement)
        for row in memory_session.execute(statement):
            hydrated_ids.append(row.id)
            yield row

    monkeypatch.setattr(upgrade, 'database', SimpleNamespace(execute=execute))
    if outcome == 'cancelled':
        job_environment.jobs_queue.get_job.return_value.cancel_event.is_set.return_value = True
    if outcome == 'profile_removed':
        catalogs = [profile_catalog(PROFILE)]
        monkeypatch.setattr(upgrade, 'load_profile_catalog', lambda: catalogs[0])

        def generate(*args, **kwargs):
            catalogs[0] = profile_catalog()
            return [SimpleNamespace(message='Upgraded')]

        job_environment.generate.side_effect = generate
    else:
        job_environment.generate.return_value = [SimpleNamespace(message='Upgraded')]
    runner = upgrade.upgrade_episodes_subtitles if media_type == 'series' else upgrade.upgrade_movies_subtitles
    if outcome == 'cancelled':
        with pytest.raises(upgrade.JobCanceled):
            runner(job_id='job-1')
    else:
        runner(job_id='job-1')

    assert hydrated_ids == [101, 102, 103, 104]
    assert len(statements) == 1
    assert len(statements[0].selected_columns) == (15 if media_type == 'series' else 11)
    sql = str(statements[0])
    assert 'EXISTS' in sql
    assert ' JOIN table_episodes_subtitles' not in sql
    assert ' JOIN table_movies_subtitles' not in sql
    expected_count = 0 if outcome == 'cancelled' else 1 if outcome == 'profile_removed' else 2
    assert job_environment.generate.call_count == expected_count
    history = upgrade.history_log if media_type == 'series' else upgrade.history_log_movie
    store = upgrade.store_subtitles if media_type == 'series' else upgrade.store_subtitles_movie
    assert [call.kwargs['upgraded_from_id'] for call in history.call_args_list] == [77, 102][:expected_count]
    assert [call.args[0] for call in store.call_args_list] == [1, 2][:expected_count]
    assert upgrade.event_stream.call_count == expected_count
    if expected_count:
        assert job_environment.generate.call_args_list[0].args[1] == [('en', 'True', 'False')]
    if expected_count == 2:
        assert job_environment.generate.call_args_list[1].args[1] == [('fr', 'False', 'False')]


@pytest.mark.parametrize('media_type', ['series', 'movie'])
def test_empty_upgrade_candidates_skip_hydration(upgrade_settings, job_environment, monkeypatch, media_type):
    database = mock.Mock()
    monkeypatch.setattr(upgrade, 'database', database)
    loader = 'get_upgradable_episode_subtitles' if media_type == 'series' else 'get_upgradable_movies_subtitles'
    monkeypatch.setattr(upgrade, loader, lambda: {})
    runner = upgrade.upgrade_episodes_subtitles if media_type == 'series' else upgrade.upgrade_movies_subtitles
    runner(job_id='job-1')
    database.execute.assert_not_called()
    job_environment.generate.assert_not_called()


@pytest.mark.parametrize('media_type', ['series', 'movie'])
def test_upgrade_hydration_batches_large_candidate_sets(
    upgrade_settings, job_environment, memory_session, monkeypatch, media_type,
):
    memory_session.add(TableLanguagesProfiles(profileId=1, name='Profile', items='[]'))
    memory_session.flush()
    if media_type == 'series':
        memory_session.add(TableShows(sonarrSeriesId=1, title='Show', path='/show', profileId=1))
        memory_session.flush()
        memory_session.add(TableEpisodes(sonarrEpisodeId=1, sonarrSeriesId=1, title='Episode', season=1, episode=1,
                                         path='/video.mkv'))
        history_model, subtitle_model = TableHistory, TableEpisodesSubtitles
        identity = {'sonarrEpisodeId': 1, 'sonarrSeriesId': 1}
    else:
        memory_session.add(TableMovies(radarrId=1, tmdbId='1', title='Movie', path='/video.mkv', profileId=1))
        history_model, subtitle_model = TableHistoryMovie, TableMoviesSubtitles
        identity = {'radarrId': 1}
    memory_session.flush()
    memory_session.add(subtitle_model(path='/subtitle.srt', language='en', hi=False, forced=False, **identity))
    for history_id in (1, 2501, 5000):
        memory_session.add(history_model(id=history_id, action=1, description='Downloaded', score=50,
                                        video_path='/video.mkv', subtitles_path='/subtitle.srt', language='en:hi',
                                        timestamp=datetime.now(), **identity))
    memory_session.flush()
    loader = 'get_upgradable_episode_subtitles' if media_type == 'series' else 'get_upgradable_movies_subtitles'
    monkeypatch.setattr(upgrade, loader, lambda: dict.fromkeys(range(2501, 0, -1)))
    statements, hydrated_ids = [], []

    def execute(statement):
        statements.append(statement)
        for row in memory_session.execute(statement):
            hydrated_ids.append(row.id)
            yield row

    monkeypatch.setattr(upgrade, 'database', SimpleNamespace(execute=execute))
    runner = upgrade.upgrade_episodes_subtitles if media_type == 'series' else upgrade.upgrade_movies_subtitles
    runner(job_id='job-1')
    assert hydrated_ids == [1, 2501]
    assert len(statements) == 6
    assert job_environment.generate.call_count == 2
    for statement in statements:
        compiled = statement.compile(compile_kwargs={'render_postcompile': True})
        assert len(compiled.params) <= 500


@pytest.mark.parametrize('media_type', ['series', 'movie'])
def test_upgrade_releases_candidate_map_before_search(
    upgrade_settings, job_environment, monkeypatch, media_type,
):
    class CandidateParents(dict):
        pass

    candidate_references = []
    row = _episode_row() if media_type == 'series' else _movie_row()

    def load_candidates():
        candidates = CandidateParents({row.id: None})
        candidate_references.append(ref(candidates))
        return candidates

    def generate(*args, **kwargs):
        assert candidate_references and candidate_references[0]() is None
        return []

    loader = 'get_upgradable_episode_subtitles' if media_type == 'series' else 'get_upgradable_movies_subtitles'
    monkeypatch.setattr(upgrade, loader, load_candidates)
    database = mock.Mock()
    database.execute.return_value = iter([row])
    monkeypatch.setattr(upgrade, 'database', database)
    job_environment.generate.side_effect = generate
    runner = upgrade.upgrade_episodes_subtitles if media_type == 'series' else upgrade.upgrade_movies_subtitles
    runner(job_id='job-1')
    job_environment.generate.assert_called_once()
