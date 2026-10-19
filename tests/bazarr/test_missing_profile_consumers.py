from types import SimpleNamespace
from app import database
from api import utils
from subtitles import manual
from subtitles import pool
from subzero.language import Language


def test_hi_download_with_missing_profile_stops_before_provider_io(mocker):
    mocker.patch.object(pool, '_update_pool')
    mocker.patch.object(manual.subtitle_cache, 'get', return_value=SimpleNamespace(
        hearing_impaired=True, language=Language('eng')))
    mocker.patch.object(database, 'update_profile_id_list', return_value=[])
    download = mocker.patch.object(manual, 'download_subtitles')
    assert manual.manual_download_subtitle('/video.mkv', [], 'True', 'False', 'cached', 'provider',
                                          None, 'Video', 'movie', False, 999) is None
    download.assert_not_called()


def test_response_with_missing_profile_keeps_external_subtitles(mocker):
    mocker.patch.object(utils.settings.general, 'embedded_subs_show_desired', True)
    mocker.patch.object(database, 'update_profile_id_list', return_value=[])
    embedded = {'name': 'English', 'code2': 'en', 'forced': False, 'path': None}
    external = {'name': 'French', 'code2': 'fr', 'forced': False, 'path': '/video.fr.srt'}
    result = utils.postprocess({'profileId': 999}, subtitles=[embedded, external])
    assert result['subtitles'] == [external]
