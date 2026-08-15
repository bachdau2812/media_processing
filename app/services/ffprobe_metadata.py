from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models.music_artifact import MusicMetadata
from app.services.process_runner import ProcessRequest
from app.services.spotiflac_downloader import ProcessRunnerLike


_MAPPED_TAGS = {
    "TITLE": "title",
    "ARTIST": "artist",
    "ALBUM": "album",
    "ALBUM_ARTIST": "album_artist",
    "COMPOSER": "composer",
    "GENRE": "genre",
    "LYRICS": "lyrics",
}


class FfprobeMetadataReader:
    def __init__(self, settings: Settings, runner: ProcessRunnerLike) -> None:
        self._settings = settings
        self._runner = runner

    async def read(self, path: Path) -> MusicMetadata:
        audio_path = Path(path)
        result = await self._runner.run(
            ProcessRequest(
                argv=(
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(audio_path),
                ),
                timeout_seconds=self._settings.music_process_timeout_seconds,
                label="ffprobe",
                track_id="unknown",
                job_id=audio_path.parent.name or "unknown",
            )
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"ffprobe failed with exit code {result.exit_code}: {result.output}"
            )
        try:
            payload = json.loads(result.output)
        except (json.JSONDecodeError, TypeError) as error:
            raise RuntimeError("ffprobe returned invalid JSON") from error

        normalized: dict[str, str | None] = {}
        streams = payload.get("streams", [])
        if isinstance(streams, list):
            for stream in streams:
                if isinstance(stream, dict):
                    normalized.update(_normalize_tags(stream.get("tags")))
        format_data = payload.get("format", {})
        if isinstance(format_data, dict):
            normalized.update(_normalize_tags(format_data.get("tags")))
        return MusicMetadata(
            **{
                field: normalized.get(tag)
                for tag, field in _MAPPED_TAGS.items()
            }
        )


def _normalize_tags(tags: Any) -> dict[str, str | None]:
    if not isinstance(tags, dict):
        return {}
    normalized: dict[str, str | None] = {}
    for key, value in tags.items():
        cleaned = str(value).strip() if value is not None else ""
        normalized[str(key).upper()] = cleaned or None
    return normalized
