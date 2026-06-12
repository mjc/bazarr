from app.database import _normalize_profile_items


def test_normalize_profile_items_converts_boolean_strings():
    items = [
        {
            "language": "en",
            "forced": "True",
            "hi": "False",
            "audio_exclude": "True",
            "audio_only_include": "False",
        },
        {
            "language": "fr",
            "forced": False,
            "hi": True,
            "audio_exclude": False,
            "audio_only_include": True,
        },
    ]

    assert _normalize_profile_items(items) == [
        {
            "language": "en",
            "forced": True,
            "hi": False,
            "audio_exclude": True,
            "audio_only_include": False,
        },
        {
            "language": "fr",
            "forced": False,
            "hi": True,
            "audio_exclude": False,
            "audio_only_include": True,
        },
    ]


def test_normalize_profile_items_drops_malformed_items():
    assert _normalize_profile_items(None) == []
    assert _normalize_profile_items([None, {"language": "en"}]) == [{"language": "en"}]
