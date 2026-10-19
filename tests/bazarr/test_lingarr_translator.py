from bazarr.subtitles.tools.translate.core.translation_request import TranslationRequest
from bazarr.subtitles.tools.translate.services import lingarr_translator
import pytest


@pytest.fixture(autouse=True)
def lingarr_settings(mocker):
    mocker.patch.object(lingarr_translator.settings.translator, 'lingarr_url', 'http://lingarr:9876', create=True)
    mocker.patch.object(lingarr_translator.settings.translator, 'lingarr_token', 'test-key', create=True)


@pytest.mark.parametrize('payload', [
    [{'position': [], 'line': 'Hello'}], [{'position': '0', 'line': 'Hello'}],
    [{'position': True, 'line': 'Hello'}], [{'position': 0, 'line': None}],
    [{'position': 0, 'line': 123}],
])
def test_malformed_translation_values_return_none(payload):
    assert lingarr_translator.parse_translation_batch(payload) is None


def _build_service(tmp_path):
    return lingarr_translator.LingarrTranslatorService(TranslationRequest(
        source_srt_file=str(tmp_path / "input.srt"),
        dest_srt_file=str(tmp_path / "output.srt"),
        target_language="fr",
        to_lang="fra",
        from_lang="en",
        media_type="movie",
        video_path="/tmp/video.mkv",
        orig_to_lang="fr",
        forced=False,
        hi=False,
        sonarr_series_id=1,
        sonarr_episode_id=1,
        radarr_id=1,
    ))


def test_lingarr_request_skips_blank_lines_and_preserves_positions(mocker, tmp_path):
    """Blank lines are skipped while original positions are preserved."""
    service = _build_service(tmp_path)

    mocker.patch.object(lingarr_translator, "get_title", return_value="Synthetic movie")

    response = mocker.Mock()
    response.status_code = 200
    response.json.return_value = [
        {"position": 0, "line": "Bonjour"},
        {"position": 3, "line": "Au revoir"},
    ]

    post = mocker.patch.object(
        lingarr_translator.requests,
        "post",
        return_value=response,
    )

    result = service._translate_content(
        ["Hello", "", "   ", "Goodbye"],
        job_id="job-1",
    )

    assert result == response.json.return_value
    assert post.call_args.kwargs["json"]["lines"] == [
        {"position": 0, "line": "Hello"},
        {"position": 3, "line": "Goodbye"},
    ]


@pytest.mark.parametrize('status,error', [
    (401, lingarr_translator.RequestError), (429, lingarr_translator.TooManyRequests),
    (500, lingarr_translator.RequestError),
])
def test_lingarr_retries_http_failures(mocker, tmp_path, status, error):
    service = _build_service(tmp_path)
    mocker.patch.object(lingarr_translator, 'get_title', return_value=None)
    mocker.patch('retry.api.time.sleep')
    post = mocker.patch.object(lingarr_translator.requests, 'post', return_value=mocker.Mock(status_code=status))
    with pytest.raises(error):
        service._translate_content(['Hello'], job_id=1)
    assert post.call_count == 3
    assert post.call_args.kwargs["headers"]["X-Api-Key"] == "test-key"


@pytest.mark.parametrize('error', [
    lingarr_translator.requests.exceptions.Timeout,
    lingarr_translator.requests.exceptions.ConnectionError,
])
def test_lingarr_retries_network_failures(mocker, tmp_path, error):
    service = _build_service(tmp_path)
    mocker.patch.object(lingarr_translator, 'get_title', return_value=None)
    mocker.patch('retry.api.time.sleep')
    post = mocker.patch.object(lingarr_translator.requests, 'post', side_effect=error)
    with pytest.raises(lingarr_translator.RequestError):
        service._translate_content(['Hello'], job_id=1)
    assert post.call_count == 3


@pytest.mark.parametrize('outcome', ['success', 'missing', 'save_failure'])
def test_lingarr_output_boundary(mocker, tmp_path, outcome):
    service = _build_service(tmp_path)
    subs = lingarr_translator.pysubs2.SSAFile.from_string(
        '1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n'
        '2\n00:00:01,000 --> 00:00:02,000\nSecond\n\n')
    mocker.patch.object(lingarr_translator.pysubs2, 'load', return_value=subs)
    mocker.patch.object(service, '_translate_content', return_value=None if outcome == 'missing' else [
        {'position': 1, 'line': ''}, {'position': 0, 'line': 'Earlier'}, {'position': 0, 'line': 'Bonjour'}])
    mocker.patch.object(lingarr_translator.jobs_queue, 'update_job_progress')
    mocker.patch.object(lingarr_translator, 'add_translator_info')
    mocker.patch.object(lingarr_translator, 'create_process_result')
    history = mocker.patch.object(lingarr_translator, 'history_log_movie')
    error = OSError('disk full')
    save = mocker.patch.object(subs, 'save', side_effect=error if outcome == 'save_failure' else None)
    if outcome == 'save_failure':
        with pytest.raises(OSError) as raised:
            service.translate(job_id=1)
        assert raised.value is error
        history.assert_not_called()
    else:
        result = service.translate(job_id=1)
        if outcome == 'missing':
            assert result is None
            save.assert_not_called()
            history.assert_not_called()
        else:
            assert result == service.request.dest_srt_file
            assert [line.text for line in subs] == ['Bonjour', 'Second']
            save.assert_called_once_with(service.request.dest_srt_file)
            history.assert_called_once()


@pytest.mark.parametrize('status,json_error', [(200, None), (200, ValueError('invalid JSON')), (400, None)])
def test_lingarr_malformed_response_is_missing_without_retry(mocker, tmp_path, status, json_error):
    service = _build_service(tmp_path)
    mocker.patch.object(lingarr_translator, 'get_title', return_value=None)
    response = mocker.Mock(status_code=status)
    response.json.side_effect = json_error
    response.json.return_value = [{'position': 0, 'line': None}]
    post = mocker.patch.object(lingarr_translator.requests, 'post', return_value=response)
    assert service._translate_content(['Hello'], job_id=1) is None
    post.assert_called_once()
