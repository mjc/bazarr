from dataclasses import dataclass
from typing import TypedDict
from typing_extensions import NotRequired


class EpisodeFile(TypedDict):
    id: int
    path: str
    size: int


class Episode(TypedDict):
    id: int
    hasFile: bool
    monitored: bool
    title: str
    episodeFileId: NotRequired[int]
    episodeFile: NotRequired[EpisodeFile]


@dataclass(frozen=True)
class CompleteEpisodeSnapshot:
    episodes: list[Episode]


def complete_episode_snapshot(episodes: list[Episode],
                              episode_files: list[EpisodeFile] | None) -> CompleteEpisodeSnapshot | None:
    if episode_files is None:
        return None
    files_by_id = {file['id']: file for file in episode_files}
    normalized: list[Episode] = []
    for episode in episodes:
        if episode['hasFile']:
            file_id = episode.get('episodeFileId')
            if file_id is None:
                return None
            file = files_by_id.get(file_id)
            if file is None:
                return None
            normalized.append({**episode, 'episodeFile': file})
        else:
            normalized.append(episode.copy())
    return CompleteEpisodeSnapshot(normalized)
