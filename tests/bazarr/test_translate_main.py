from bazarr.subtitles.tools.translate import main


def test_translation_skips_postprocessing_when_service_returns_none(mocker):
    lang_obj = mocker.Mock()
    lang_obj.alpha2 = "fr"
    lang_obj.subzero_language.return_value = "fr"
    translator = mocker.Mock()
    translator.translate.return_value = None

    mocker.patch.object(main, "validate_translation_params")
    mocker.patch.object(main, "convert_language_codes", return_value=(lang_obj, "fr"))
    mocker.patch.object(main, "get_translation_extension", return_value="srt")
    mocker.patch.object(main, "get_subtitle_path", return_value="/media/video.fr.srt")
    mocker.patch.object(main, "get_target_folder", return_value=None)
    mocker.patch.object(main, "alpha3_from_alpha2", return_value="fra")
    mocker.patch.object(main.TranslatorFactory, "create_translator", return_value=translator)
    mocker.patch.object(main.jobs_queue, "update_job_name")
    postprocess = mocker.patch("api.subtitles.subtitles.postprocess_subtitles")

    result = main.translate_subtitles_file(
        video_path="/media/video.mkv",
        source_srt_file="/media/source.srt",
        from_lang="en",
        to_lang="fr",
        forced=False,
        hi=False,
        media_type="movie",
        sonarr_series_id=None,
        sonarr_episode_id=None,
        radarr_id=1,
        metadata={},
        job_id=1,
    )

    translator.translate.assert_called_once_with(job_id=1)
    assert result is None
    postprocess.assert_not_called()
