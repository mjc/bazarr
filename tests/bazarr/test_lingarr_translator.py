from languages.registry import ResolvedLanguage
import pytest
from deep_translator.exceptions import RequestError, TooManyRequests
from utilities.json_types import JsonValue

from bazarr.subtitles.tools.translate.core.translation_request import (
    LingarrConfig, MovieMedia, TranslationRequest, TranslationTarget,
)
from bazarr.subtitles.tools.translate.services import lingarr_translator


def _build_service(tmp_path):
    request = TranslationRequest(str(tmp_path / "input.srt"), str(tmp_path / "output.srt"), '/media/video.mkv', 'en',
                                 TranslationTarget(ResolvedLanguage('fr', 'fra', 'French'), 'fr', 'fr'), MovieMedia(1), False, False)
    return lingarr_translator.LingarrTranslatorService(request, LingarrConfig(False, 50, 'http://lingarr:9876', ''))


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
    )

    assert result == {0: "Bonjour", 3: "Au revoir"}
    assert post.call_args.kwargs["json"]["lines"] == [
        {"position": 0, "line": "Hello"},
        {"position": 3, "line": "Goodbye"},
    ]


@pytest.mark.parametrize('payload', [
    None, {}, 'text', [None], [{'line': 'Hello'}], [{'position': 0}],
    [{'position': [], 'line': 'Hello'}], [{'position': '0', 'line': 'Hello'}],
    [{'position': True, 'line': 'Hello'}], [{'position': 0.5, 'line': 'Hello'}],
    [{'position': 0, 'line': None}], [{'position': 0, 'line': 123}],
])
def test_invalid_lingarr_values_are_rejected_at_boundary(payload: JsonValue):
    assert lingarr_translator.parse_translation_batch(payload) is None


def test_translation_map_keeps_last_duplicate_and_empty_values():
    assert lingarr_translator.parse_translation_batch([
        {'position': 3, 'line': 'later'}, {'position': 0, 'line': 'earlier'},
        {'position': 0, 'line': ''}, {'position': 3, 'line': 'final'},
    ]) == {0: '', 3: 'final'}


@pytest.mark.parametrize('status,error', [(401, RequestError), (429, TooManyRequests), (500, RequestError)])
def test_http_failures_retry_with_one_payload_and_title_lookup(mocker, tmp_path, status, error):
    service = _build_service(tmp_path)
    title = mocker.patch.object(lingarr_translator, 'get_title', return_value='')
    mocker.patch('retry.api.time.sleep')
    post = mocker.patch.object(lingarr_translator.requests, 'post', return_value=mocker.Mock(status_code=status))
    with pytest.raises(error):
        service._translate_content(iter(['Hello', '', 'Bye']))
    assert post.call_count == 3
    title.assert_called_once()
    payloads = [call.kwargs['json'] for call in post.call_args_list]
    assert all(payload is payloads[0] for payload in payloads)
    assert payloads[0]['lines'] == [{'position': 0, 'line': 'Hello'}, {'position': 2, 'line': 'Bye'}]


@pytest.mark.parametrize('error', [lingarr_translator.requests.Timeout, lingarr_translator.requests.ConnectionError])
def test_network_failures_retry_without_rebuilding_payload(mocker, tmp_path, error):
    service = _build_service(tmp_path)
    mocker.patch.object(lingarr_translator, 'get_title', return_value='')
    mocker.patch('retry.api.time.sleep')
    post = mocker.patch.object(lingarr_translator.requests, 'post', side_effect=error('network'))
    with pytest.raises(error):
        service._translate_content(['Hello'])
    assert post.call_count == 3


@pytest.mark.parametrize('status,payload,json_error', [
    (200, [{'position': 0, 'line': None}], None), (200, None, ValueError('invalid JSON')), (400, None, None),
])
def test_invalid_response_does_not_retry(mocker, tmp_path, status, payload, json_error):
    service = _build_service(tmp_path)
    mocker.patch.object(lingarr_translator, 'get_title', return_value='')
    response = mocker.Mock(status_code=status)
    response.json.return_value = payload
    response.json.side_effect = json_error
    post = mocker.patch.object(lingarr_translator.requests, 'post', return_value=response)
    with pytest.raises(RequestError):
        service._translate_content(['Hello'])
    post.assert_called_once()


def test_saved_output_preserves_sparse_duplicate_blank_and_unmatched_positions(mocker, tmp_path):
    service = _build_service(tmp_path)
    source = tmp_path / 'input.srt'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n'
                      '2\n00:00:01,000 --> 00:00:02,000\nSecond\n\n'
                      '3\n00:00:02,000 --> 00:00:03,000\nThird\n\n')
    mocker.patch.object(lingarr_translator, 'get_title', return_value='')
    response = mocker.Mock(status_code=200)
    response.json.return_value = [
        {'position': 2, 'line': 'Trois'}, {'position': 0, 'line': 'Earlier'},
        {'position': 1, 'line': 'Deux'}, {'position': 1, 'line': ''},
        {'position': 0, 'line': 'Un'}, {'position': -1, 'line': 'ignored'},
        {'position': 100, 'line': 'ignored'},
    ]
    mocker.patch.object(lingarr_translator.requests, 'post', return_value=response)
    mocker.patch.object(lingarr_translator.jobs_queue, 'update_job_progress')
    output = service.translate(job_id=1)
    assert [line.text for line in lingarr_translator.pysubs2.load(output)] == ['Un', 'Second', 'Trois']


def test_empty_input_is_saved_without_a_remote_request(mocker, tmp_path):
    service = _build_service(tmp_path)
    (tmp_path / 'input.srt').write_text('')
    mocker.patch.object(lingarr_translator.pysubs2, 'load', return_value=lingarr_translator.pysubs2.SSAFile())
    post = mocker.patch.object(lingarr_translator.requests, 'post')
    mocker.patch.object(lingarr_translator.jobs_queue, 'update_job_progress')
    output = service.translate(job_id=1)
    assert output == str(tmp_path / 'output.srt')
    assert (tmp_path / 'output.srt').exists()
    post.assert_not_called()


def test_save_failure_retains_original_exception(mocker, tmp_path):
    service = _build_service(tmp_path)
    (tmp_path / 'input.srt').write_text('')
    mocker.patch.object(lingarr_translator.pysubs2, 'load', return_value=lingarr_translator.pysubs2.SSAFile())
    error = OSError('disk full')
    mocker.patch.object(lingarr_translator.pysubs2.SSAFile, 'save', side_effect=error)
    mocker.patch.object(lingarr_translator.jobs_queue, 'update_job_progress')
    with pytest.raises(OSError) as raised:
        service.translate(job_id=1)
    assert raised.value is error
