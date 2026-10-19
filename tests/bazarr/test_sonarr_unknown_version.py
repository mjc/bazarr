from sonarr.sync import episodes


def test_unknown_sonarr_version_skips_sync_without_reconciliation(mocker):
    database = mocker.patch.object(episodes, 'database')
    database.execute.return_value.all.return_value = []
    mocker.patch.object(episodes, 'get_episodes_from_sonarr_api', return_value=[{'id': 1, 'hasFile': True}])
    mocker.patch.object(episodes.get_sonarr_info, 'is_legacy', return_value=False)
    mocker.patch.object(episodes.get_sonarr_info, 'semver', return_value=None)
    files = mocker.patch.object(episodes, 'get_episodesFiles_from_sonarr_api')
    reconcile = mocker.patch.object(episodes, 'delete_media_and_wanted_search_state')
    episodes.sync_episodes(1)
    files.assert_not_called()
    reconcile.assert_not_called()
    assert database.execute.call_count == 1
