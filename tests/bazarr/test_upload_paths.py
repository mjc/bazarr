from languages.registry import LanguageRegistry
from io import BytesIO
from types import SimpleNamespace

import pytest

from languages import get_languages
import subtitles.upload as upload


@pytest.fixture
def upload_module(bind_wanted_database, monkeypatch):
    module = upload
    bind_wanted_database(module, "movies")
    bind_wanted_database(module, "series")

    monkeypatch.setattr(
        get_languages,
        "_language_registry",
        LanguageRegistry([{"code2": "en", "code3": "eng", "code3b": None, "name": "English"}]),
        raising=False,
    )
    monkeypatch.setattr(module, "set_chmod", lambda **kwargs: None)
    monkeypatch.setattr(module, "sync_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "postprocessing", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "notify_sonarr", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "notify_radarr", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "history_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "history_log_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "store_subtitles", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "store_subtitles_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "event_stream", lambda **kwargs: None)
    monkeypatch.setattr(module, "send_notifications", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "send_notifications_movie", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "save_subtitles",
        lambda *args, **kwargs: [SimpleNamespace(storage_path="/subs/sub.srt")],
    )
    monkeypatch.setattr(module.settings.general, "single_language", False)
    monkeypatch.setattr(module.settings.general, "use_postprocessing", False)
    monkeypatch.setattr(module.settings.general, "postprocessing_cmd", "")
    monkeypatch.setattr(module.settings.general, "chmod_enabled", False)
    monkeypatch.setattr(module.settings.general, "utf8_encode", False)
    monkeypatch.setattr(module.settings.general, "subzero_mods", [])
    monkeypatch.setattr(module.settings.general, "dont_notify_manual_actions", True)
    monkeypatch.setattr(module.settings.general, "use_plex", False)
    monkeypatch.setattr(module.settings.general, "use_jellyfin", False)
    return module


def _subtitle_file():
    return BytesIO(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")


def test_series_upload_allows_missing_profile_and_audio_language(
    upload_module, episode_row_factory, monkeypatch
):
    episode_row_factory(sonarrSeriesId=5, sonarrEpisodeId=11, profileId=44)

    monkeypatch.setattr(upload_module, "get_profile", lambda profile_id: None)
    monkeypatch.setattr(upload_module, "get_audio_profile_languages", lambda audio_language: None)

    result = upload_module.manual_upload_subtitle(
        path="/series/episode.mkv",
        language="en",
        forced=False,
        hi=False,
        media_type="series",
        subtitle=_subtitle_file(),
        filename="subtitle.srt",
        audio_language="['eng']",
        job_id=1,
        sonarrSeriesId=5,
        sonarrEpisodeId=11,
    )

    assert result == ("", 204)


def test_movie_upload_allows_missing_profile_and_audio_language(
    upload_module, movie_row_factory, monkeypatch
):
    movie_row_factory(radarrId=7, profileId=44)

    monkeypatch.setattr(upload_module, "get_profile", lambda profile_id: None)
    monkeypatch.setattr(upload_module, "get_audio_profile_languages", lambda audio_language: None)

    result = upload_module.manual_upload_subtitle(
        path="/movies/movie.mkv",
        language="en",
        forced=False,
        hi=False,
        media_type="movie",
        subtitle=_subtitle_file(),
        filename="subtitle.srt",
        audio_language="['eng']",
        job_id=1,
        radarrId=7,
    )

    assert result == ("", 204)


def test_manual_upload_subtitle_postprocessing_handles_nullable_audio_language_metadata(
    upload_module, movie_row_factory, monkeypatch
):
    movie_row_factory(radarrId=7, profileId=44)
    commands = []

    monkeypatch.setattr(upload_module.settings.general, "use_postprocessing", True)
    monkeypatch.setattr(upload_module, "get_profile", lambda profile_id: None)
    monkeypatch.setattr(upload_module, "get_audio_profile_languages", lambda audio_language: [{"name": "English", "code2": None, "code3": None}])
    monkeypatch.setattr(upload_module, "pp_replace", lambda *args, **kwargs: commands.append(args) or "cmd")

    result = upload_module.manual_upload_subtitle(
        path="/movies/movie.mkv",
        language="en",
        forced=False,
        hi=False,
        media_type="movie",
        subtitle=_subtitle_file(),
        filename="subtitle.srt",
        audio_language="['eng']",
        job_id=1,
        radarrId=7,
    )

    assert result == ("", 204)
    assert len(commands) == 1


@pytest.mark.parametrize('field', ['code2', 'code3', 'name'])
def test_incomplete_upload_language_stops_before_saving(upload_module, movie_row_factory, monkeypatch, mocker, field):
    movie_row_factory(radarrId=7, profileId=44)
    entry = {'code2': 'en', 'code3': 'eng', 'code3b': None, 'name': 'English'}
    entry[field] = None
    monkeypatch.setattr(get_languages, '_language_registry', LanguageRegistry([entry]))
    save = mocker.patch.object(upload_module, 'save_subtitles')
    assert upload_module.manual_upload_subtitle('/movie.mkv', 'en', False, False, 'movie', _subtitle_file(),
                                                'sub.srt', '[]', job_id=1, radarrId=7) is None
    save.assert_not_called()


def test_upload_enqueues_before_language_resolution(upload_module, monkeypatch, mocker):
    enqueue = mocker.patch.object(upload_module.jobs_queue, 'add_job_from_function', return_value=37)
    resolve = mocker.patch.object(upload_module, 'resolve_language')
    assert upload_module.manual_upload_subtitle('/movie.mkv', None, False, False, 'movie', _subtitle_file(),
                                                'sub.srt', '[]') == 37
    enqueue.assert_called_once_with('Uploading sub.srt', is_progress=False)
    resolve.assert_not_called()


@pytest.mark.parametrize('code,code3,name', [('pb', 'pob', 'Portuguese (Brazil)'),
                                           ('zt', 'zht', 'Chinese Traditional'), ('ea', 'spl', 'Spanish (Latino)')])
@pytest.mark.parametrize('hi,forced,suffix,label', [(False, False, '', ''), (True, False, ':hi', ' HI'),
                                                  (False, True, ':forced', ' forced')])
def test_upload_reuses_custom_language_identity_in_all_outputs(
    upload_module, movie_row_factory, monkeypatch, mocker, code, code3, name, hi, forced, suffix, label,
):
    movie_row_factory(radarrId=7, profileId=44)
    monkeypatch.setattr(get_languages, '_language_registry', LanguageRegistry([
        {'code2': code, 'code3': code3, 'name': name, 'code3b': None},
    ]))
    resolve = mocker.spy(upload_module, 'resolve_language')
    sync = mocker.patch.object(upload_module, 'sync_subtitles')
    history = mocker.patch.object(upload_module, 'history_log_movie')
    command = mocker.patch.object(upload_module, 'pp_replace', return_value='command')
    monkeypatch.setattr(upload_module.settings.general, 'use_postprocessing', True)
    assert upload_module.manual_upload_subtitle('/movie.mkv', code, forced, hi, 'movie', _subtitle_file(),
                                                'sub.srt', '[]', job_id=1, radarrId=7) == ('', 204)
    resolve.assert_called_once_with(code)
    assert sync.call_args.kwargs['srt_lang'] == code + suffix
    assert command.call_args.args[3:6] == (name + label, code + suffix, code3 + suffix)
    assert history.call_args.args[2].language_code == code + suffix
    assert history.call_args.args[2].message == f'{name}{label} Subtitles manually uploaded.'


@pytest.mark.parametrize('format_', ['ass', 'ssa', 'vtt', 'srt'])
@pytest.mark.parametrize('original', [False, True])
@pytest.mark.parametrize('wrong_filename', [False, True])
def test_upload_format_follows_content_and_original_format_setting(
    upload_module, movie_row_factory, monkeypatch, tmp_path, format_, original, wrong_filename,
):
    import pysubs2
    from subliminal_patch.core import save_subtitles as real_save_subtitles
    from tests.helpers.profile_records import profile_record

    movie_row_factory(radarrId=7, profileId=44)
    monkeypatch.setattr(upload_module, 'get_profile', lambda profile_id: profile_record({'originalFormat': original}))
    monkeypatch.setattr(upload_module, 'get_target_folder', lambda path: str(tmp_path))
    document = pysubs2.SSAFile()
    document.events.append(pysubs2.SSAEvent(start=0, end=1000, text='Hello'))
    content = document.to_string(format_).encode('utf-8')
    observed = []

    def save(path, subtitles, **kwargs):
        observed.append((subtitles[0].format, kwargs['formats']))
        assert isinstance(subtitles[0].format, str)
        return real_save_subtitles(path, subtitles, **kwargs)

    monkeypatch.setattr(upload_module, 'save_subtitles', save)
    extension = 'srt' if wrong_filename and format_ != 'srt' else 'ass' if wrong_filename else format_
    assert upload_module.manual_upload_subtitle('/movie.mkv', 'en', False, False, 'movie', BytesIO(content),
                                                'subtitle.' + extension, '[]', job_id=1, radarrId=7) == ('', 204)
    expected = format_ if original else 'srt'
    assert observed == [(expected, (expected,))]
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].suffix == '.' + expected
    assert pysubs2.load(str(files[0])).events[0].text == 'Hello'


@pytest.mark.parametrize('language', [None, '', 'xx'])
def test_unknown_upload_language_stops_before_database_and_save(upload_module, mocker, language):
    metadata = mocker.patch.object(upload_module, '_load_metadata')
    save = mocker.patch.object(upload_module, 'save_subtitles')
    assert upload_module.manual_upload_subtitle('/movie.mkv', language, False, False, 'movie', _subtitle_file(),
                                                'subtitle.srt', '[]', job_id=1, radarrId=7) is None
    metadata.assert_not_called()
    save.assert_not_called()


def test_invalid_upload_content_stops_before_save(upload_module, movie_row_factory, mocker):
    movie_row_factory(radarrId=7, profileId=44)
    save = mocker.patch.object(upload_module, 'save_subtitles')
    assert upload_module.manual_upload_subtitle('/movie.mkv', 'en', False, False, 'movie', BytesIO(b'garbage'),
                                                'subtitle.srt', '[]', job_id=1, radarrId=7) is None
    save.assert_not_called()
