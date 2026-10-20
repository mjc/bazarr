# coding=utf-8

from collections.abc import Collection, Iterable, Mapping, Sequence

from utilities.arr_types import ArrTag, AudioProfile, KEEP_PROFILE, ProfileAssignment, ProfileId


def default_profile_id(configured: int | str) -> int | None:
    try:
        return int(configured)
    except ValueError:
        return None


class ArrSyncLookups:
    """Index the tag and profile lists fetched for one sync."""

    def __init__(self, tags: Iterable[ArrTag], language_profiles: Iterable[tuple[int, str, str | None]],
                 audio_profiles: Iterable[AudioProfile]) -> None:
        self._tags: dict[int, list[tuple[int, str]]] = {}
        for position, tag in enumerate(tags):
            self._tags.setdefault(tag['id'], []).append((position, tag['label']))
        self._profiles: dict[str, tuple[int, int]] = {}
        for position, (profile_id, name, tag) in enumerate(language_profiles):
            if tag is not None:
                self._profiles.setdefault(tag, (position, profile_id))
        audio: dict[ProfileId, list[str]] = {}
        for identifier, language in audio_profiles:
            audio.setdefault(identifier, []).append(language)
        self.audio_language_values: Mapping[ProfileId, str] = {
            identifier: str(languages) for identifier, languages in audio.items()}

    def tags(self, identifiers: Iterable[int]) -> list[str]:
        matches = (entry for identifier in set(identifiers) for entry in self._tags.get(identifier, ()))
        return [label for position, label in sorted(matches)]

    def profile_id(self, tags: Sequence[str], default: ProfileAssignment, *, enabled: bool,
                   remove_tags: Collection[str]) -> ProfileAssignment:
        if not enabled:
            return default
        for tag in tags:
            if tag in remove_tags:
                return None
        match = min((self._profiles[tag] for tag in tags if tag in self._profiles), default=None)
        return (match[1] if match is not None else None) or default
