from languages.registry import ResolvedLanguage
from bazarr.subtitles.tools.translate.core.translation_request import (
    MovieMedia, TranslationRequest, TranslationTarget,
)
import pytest

from bazarr.subtitles.tools.translate.services import google_translator
from deep_translator.exceptions import RequestError


def _build_service(tmp_path):
    request = TranslationRequest(str(tmp_path / "input.srt"), str(tmp_path / "output.srt"), '/media/video.mkv', 'en',
                                 TranslationTarget(ResolvedLanguage('fr', 'fra', 'French'), 'fr', 'fr'), MovieMedia(1), False, False)
    return google_translator.GoogleTranslatorService(request)


def test_looks_like_error_response_detects_html_and_error_text():
    assert google_translator.GoogleTranslatorService._looks_like_error_response(
        "<html><body>Error 500</body></html>")
    assert google_translator.GoogleTranslatorService._looks_like_error_response(
        "Too Many Requests")
    assert google_translator.GoogleTranslatorService._looks_like_error_response(
        "Error 500: Internal Server Error")
    assert google_translator.GoogleTranslatorService._looks_like_error_response(
        "<div class='captcha'>unusual traffic</div>")


def test_looks_like_error_response_leaves_real_translations_alone():
    assert not google_translator.GoogleTranslatorService._looks_like_error_response("Bonjour le monde")
    assert not google_translator.GoogleTranslatorService._looks_like_error_response("¿Qué hora es?")
    # An unrelated "500" inside normal text must not be flagged as an error.
    assert not google_translator.GoogleTranslatorService._looks_like_error_response(
        "The reward is 500 dollars.")


def test_translate_text_raises_on_error_response_baked_into_result(mocker, tmp_path):
    """
    Regression: the free Google endpoint can answer 200 OK with an error/captcha page, and
    deep_translator returns that error text as the translation. The translator must reject it
    (raising RequestError so @retry can retry) instead of baking "Error 500" into the subtitle.
    """
    service = _build_service(tmp_path)
    mocker.patch("retry.api.time.sleep")
    mocker.patch.object(google_translator, "GoogleTranslator")

    # Force the inner retry to give up quickly so the test stays fast.
    mocker.patch.object(
        google_translator,
        "retry",
        lambda *a, **kw: (lambda f: f),
    )

    google_translator.GoogleTranslator.return_value.translate.return_value = "Error 500: Server Error"
    mocker.patch.object(google_translator.jobs_queue, "update_job_progress")

    with pytest.raises(RequestError):
        service._translate_text("Hello world", job_id="job-1")


def test_translate_aborts_when_lines_fail_instead_of_writing_partial_file(mocker, tmp_path):
    """
    Regression: per-line failures used to be swallowed by the futures loop, the subtitle was
    saved with untranslated lines, and the job reported success. We now collect failures and
    raise so no partially-translated subtitle is written.
    """
    input_file = tmp_path / "input.srt"
    output_file = tmp_path / "output.srt"
    input_file.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello world\n\n", encoding="utf-8")

    service = _build_service(tmp_path)

    # Make every line fail to translate.
    mocker.patch.object(service, "_translate_text", side_effect=RequestError("boom"))
    mocker.patch.object(google_translator.jobs_queue, "update_job_progress")
    # Guard: saving must never happen when translation failed.
    save_mock = mocker.patch("pysubs2.SSAFile.save", autospec=True)

    with pytest.raises(RequestError, match="1/1 line"):
        service.translate(job_id="job-1")

    save_mock.assert_not_called()


def test_translate_succeeds_when_all_lines_translate(mocker, tmp_path):
    """Happy path: when translation succeeds the output file is written and the path returned."""
    input_file = tmp_path / "input.srt"
    output_file = tmp_path / "output.srt"
    input_file.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello world\n\n", encoding="utf-8")

    service = _build_service(tmp_path)
    mocker.patch.object(service, "_translate_text", return_value="Bonjour le monde")
    mocker.patch.object(google_translator.jobs_queue, "update_job_progress")

    result = service.translate(job_id="job-1")

    assert result == str(output_file)
    assert output_file.exists()


def test_out_of_order_completion_keeps_subtitle_positions_and_progress(mocker, tmp_path):
    from threading import Event
    input_file = tmp_path / 'input.srt'
    input_file.write_text('1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n'
                          '2\n00:00:01,000 --> 00:00:02,000\nSecond\n\n')
    service = _build_service(tmp_path)
    later_finished = Event()
    completion_order = []
    def translate(text: str, job_id: int | None) -> str:
        if text == 'First':
            assert later_finished.wait(timeout=3)
            completion_order.append(text)
            return 'Premier'
        completion_order.append(text)
        later_finished.set()
        return 'Deuxième'
    mocker.patch.object(service, '_translate_text', side_effect=translate)
    progress = mocker.patch.object(google_translator.jobs_queue, 'update_job_progress')
    executor = mocker.spy(google_translator, 'ThreadPoolExecutor')
    output = service.translate(job_id=1)
    assert completion_order == ['Second', 'First']
    assert [event.text for event in google_translator.pysubs2.load(output)] == ['Premier', 'Deuxième']
    assert [call.kwargs['progress_value'] for call in progress.call_args_list if 'progress_value' in call.kwargs] == [1, 2]
    executor.assert_called_once_with(max_workers=10)


def test_all_failures_are_counted_without_saving_successful_lines(mocker, tmp_path):
    input_file = tmp_path / 'input.srt'
    input_file.write_text('1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n'
                          '2\n00:00:01,000 --> 00:00:02,000\nSecond\n\n'
                          '3\n00:00:02,000 --> 00:00:03,000\nThird\n\n')
    service = _build_service(tmp_path)
    def translate(text: str, job_id: int | None) -> str:
        if text != 'Second':
            raise RequestError('failed')
        return 'Deuxième'
    mocker.patch.object(service, '_translate_text', side_effect=translate)
    progress = mocker.patch.object(google_translator.jobs_queue, 'update_job_progress')
    save = mocker.patch.object(google_translator.pysubs2.SSAFile, 'save')
    with pytest.raises(RequestError, match='2/3 line'):
        service.translate(job_id=1)
    save.assert_not_called()
    assert [call.kwargs['progress_value'] for call in progress.call_args_list if 'progress_value' in call.kwargs] == [1, 2, 3]


@pytest.mark.parametrize('result', ['', 'not_found'])
def test_blank_and_missing_translation_preserve_original_text(mocker, tmp_path, result):
    (tmp_path / 'input.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nOriginal\n\n')
    service = _build_service(tmp_path)
    if result == 'not_found':
        translate = mocker.patch.object(service, '_translate_text',
                                        side_effect=google_translator.TranslationNotFound('Original'))
    else:
        translate = mocker.patch.object(service, '_translate_text', return_value='')
    mocker.patch.object(google_translator.jobs_queue, 'update_job_progress')
    output = service.translate(job_id=1)
    assert [event.text for event in google_translator.pysubs2.load(output)] == ['Original']
    translate.assert_called_once_with('Original', 1)


def test_google_save_failure_propagates_original_exception(mocker, tmp_path):
    (tmp_path / 'input.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nOriginal\n\n')
    service = _build_service(tmp_path)
    mocker.patch.object(service, '_translate_text', return_value='Traduit')
    mocker.patch.object(google_translator.jobs_queue, 'update_job_progress')
    error = OSError('disk full')
    mocker.patch.object(google_translator.pysubs2.SSAFile, 'save', side_effect=error)
    with pytest.raises(OSError) as raised:
        service.translate(job_id=1)
    assert raised.value is error
