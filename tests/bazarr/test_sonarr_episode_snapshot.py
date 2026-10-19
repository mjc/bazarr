import semver
import pytest
from unittest.mock import Mock

from app import database as db
from sonarr.sync import episodes as sync
from sonarr.episode_snapshot import complete_episode_snapshot


@pytest.mark.parametrize("files", [None, [], [{"id": 99, "path": "/other.mkv", "size": 100}]])
def test_incomplete_episode_files_do_not_authorize_reconciliation(files):
    episodes = [{"id": 101, "hasFile": True, "episodeFileId": 11}]
    assert complete_episode_snapshot(episodes, files) is None


def test_complete_snapshot_attaches_files():
    file = {"id": 11, "path": "/episode.mkv", "size": 100}
    result = complete_episode_snapshot([{"id": 101, "hasFile": True, "episodeFileId": 11}], [file])
    assert result is not None
    assert result.episodes[0]["episodeFile"] == file


def test_successful_empty_files_for_episodes_without_media_is_complete():
    assert complete_episode_snapshot([{"id": 101, "hasFile": False}], []) is not None


@pytest.mark.parametrize("files", [None, []])
def test_failed_secondary_fetch_preserves_existing_episode(memory_session, monkeypatch, files):
    memory_session.add(db.TableShows(sonarrSeriesId=7, title="Series", path="/series"))
    memory_session.flush()
    memory_session.add(db.TableEpisodes(sonarrSeriesId=7, sonarrEpisodeId=101, path="/episode.mkv",
                                        season=1, episode=1, title="Pilot"))
    memory_session.commit()
    monkeypatch.setattr(sync, "database", memory_session)
    monkeypatch.setattr(sync.get_sonarr_info, "is_legacy", lambda: False)
    monkeypatch.setattr(sync.get_sonarr_info, "semver", lambda: semver.Version(3, 0, 0))
    monkeypatch.setattr(sync.settings.sonarr, "sync_only_monitored_series", False)
    monkeypatch.setattr(sync, "get_episodes_from_sonarr_api", lambda **kwargs: [
        {"id": 101, "hasFile": True, "episodeFileId": 11}])
    monkeypatch.setattr(sync, "get_episodesFiles_from_sonarr_api", lambda **kwargs: files)
    delete = Mock(return_value=[])
    monkeypatch.setattr(sync, "delete_media_and_wanted_search_state", delete)

    sync.sync_episodes(7)

    delete.assert_not_called()
    assert memory_session.get(db.TableEpisodes, 101) is not None
