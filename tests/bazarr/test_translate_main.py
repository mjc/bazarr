import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dynaconf.utils.boxing import DynaBox

from bazarr.subtitles.tools.translate import main
from bazarr.subtitles.tools.translate.core.translation_request import EpisodeMedia, MovieMedia


def setup_operation(monkeypatch, tmp_path, translator_type='google_translate'):
    source = tmp_path / 'input.srt'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\nHello\n\n')
    destination = tmp_path / 'output.srt'
    section = DynaBox({'translator_type': translator_type, 'translator_info': False, 'default_score': 50,
                       'lingarr_url': 'http://lingarr', 'lingarr_token': '',
                       'gemini_model': 'gemini', 'gemini_keys': ['key'], 'gemini_batch_size': 20})
    monkeypatch.setattr(main, 'settings', SimpleNamespace(translator=section))
    monkeypatch.setattr(main, 'get_target_folder', lambda video: None)
    monkeypatch.setattr(main, 'get_subtitle_path', lambda *args, **kwargs: str(destination))
    monkeypatch.setattr(main.jobs_queue, 'update_job_name', Mock())
    history = Mock()
    movie_history = Mock()
    monkeypatch.setattr(main, 'history_log', history)
    monkeypatch.setattr(main, 'history_log_movie', movie_history)
    monkeypatch.setattr(main, 'create_process_result', Mock(return_value='history-result'))
    import api.subtitles.subtitles as subtitles_api
    postprocess = Mock()
    monkeypatch.setattr(subtitles_api, 'postprocess_subtitles', postprocess)
    kwargs = dict(video_path=str(tmp_path / 'video.mkv'), source_srt_file=str(source), from_lang='en',
                  to_lang='fr', forced=False, hi=False, media_type='movie', sonarr_series_id=None,
                  sonarr_episode_id=7, radarr_id=7, metadata={}, job_id=1)
    return kwargs, section, destination, history, movie_history, postprocess


@pytest.mark.parametrize('translator_type', ['', 'google_translate', 'gemini', 'lingarr'])
def test_saved_output_is_required_before_shared_completion(monkeypatch, tmp_path, translator_type):
    kwargs, section, destination, history, movie_history, postprocess = setup_operation(
        monkeypatch, tmp_path, translator_type)
    def create_service(request, config):
        assert isinstance(request.media, MovieMedia)
        assert request.media.movie_id == 7
        assert config.name == (translator_type or 'google_translate')
        def save(job_id):
            destination.write_text('Translated')
            return str(destination)
        return SimpleNamespace(translate=save)
    monkeypatch.setattr(main.TranslatorFactory, 'create_translator', create_service)
    assert main.translate_subtitles_file(**kwargs) == str(destination)
    history.assert_not_called()
    movie_history.assert_called_once_with(action=6, radarr_id=7, result='history-result')
    postprocess.assert_called_once_with(str(destination), 'movie', {}, 7)


@pytest.mark.parametrize('outcome', ['error', 'missing_output', 'annotation_error'])
def test_failed_translation_has_no_history_or_postprocessing(monkeypatch, tmp_path, outcome):
    kwargs, section, destination, history, movie_history, postprocess = setup_operation(monkeypatch, tmp_path)
    error = OSError('disk full')
    def translate(job_id):
        if outcome == 'error':
            raise error
        if outcome == 'annotation_error':
            destination.write_text('Translated')
        return str(destination)
    monkeypatch.setattr(main.TranslatorFactory, 'create_translator',
                        lambda *args: SimpleNamespace(translate=translate))
    if outcome == 'annotation_error':
        section.translator_info = True
        monkeypatch.setattr(main, 'add_translator_info', Mock(side_effect=error))
    with pytest.raises(OSError) as raised:
        main.translate_subtitles_file(**kwargs)
    if outcome != 'missing_output':
        assert raised.value is error
    history.assert_not_called()
    movie_history.assert_not_called()
    postprocess.assert_not_called()
    main.jobs_queue.update_job_name.assert_not_called()


def test_media_target_and_config_are_resolved_once_per_operation(monkeypatch, tmp_path):
    kwargs, section, destination, history, movie_history, postprocess = setup_operation(monkeypatch, tmp_path)
    kwargs.update(media_type='episode', sonarr_series_id=2, sonarr_episode_id=3, radarr_id=99, to_lang='pb')
    conversions = Mock(wraps=main.convert_language_codes)
    monkeypatch.setattr(main, 'convert_language_codes', conversions)
    def create_service(request, config):
        assert request.media == EpisodeMedia(2, 3)
        assert request.target.language.code2 == 'pb'
        assert request.target.language.code3 == 'pob'
        section.default_score = 99
        def save(job_id):
            destination.write_text('Translated')
            return str(destination)
        return SimpleNamespace(translate=save)
    monkeypatch.setattr(main.TranslatorFactory, 'create_translator', create_service)
    main.translate_subtitles_file(**kwargs)
    conversions.assert_called_once_with('pb', False, False)
    assert main.create_process_result.call_args.args[-1] == 50
    history.assert_called_once_with(action=6, sonarr_series_id=2, sonarr_episode_id=3, result='history-result')
    movie_history.assert_not_called()


@pytest.mark.parametrize('function', [main.translate_subtitles_file, main.extract_and_translate_embedded])
def test_real_enqueue_binds_only_signature_parameters(monkeypatch, tmp_path, function):
    kwargs, section, destination, history, movie_history, postprocess = setup_operation(monkeypatch, tmp_path)
    kwargs['job_id'] = None
    if function is main.extract_and_translate_embedded:
        kwargs.pop('source_srt_file')
        kwargs['subtitles_id'] = 1
    feed = Mock(return_value=1)
    monkeypatch.setattr(main.jobs_queue, 'feed_jobs_pending_queue', feed)
    assert function(**kwargs) is None
    feed.assert_called_once()
    bound = feed.call_args.kwargs['kwargs']
    assert set(bound) == set(inspect.signature(function).parameters)
    assert not destination.exists()
    history.assert_not_called()
    movie_history.assert_not_called()
    postprocess.assert_not_called()


def test_missing_output_skips_completion_and_success_label(monkeypatch, tmp_path):
    kwargs, section, destination, history, movie_history, postprocess = setup_operation(monkeypatch, tmp_path)
    monkeypatch.setattr(main.TranslatorFactory, 'create_translator',
                        lambda *args: SimpleNamespace(translate=lambda job_id: None))
    assert main.translate_subtitles_file(**kwargs) is None
    assert not destination.exists()
    history.assert_not_called()
    movie_history.assert_not_called()
    postprocess.assert_not_called()
    main.jobs_queue.update_job_name.assert_not_called()
