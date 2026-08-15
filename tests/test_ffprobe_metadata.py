import json
from pathlib import Path

import pytest

from app.config import Settings
from app.models.music_artifact import MusicMetadata
from app.services.ffprobe_metadata import FfprobeMetadataReader
from app.services.process_runner import ProcessRequest, ProcessResult


class CapturingRunner:
    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        self.requests: list[ProcessRequest] = []

    async def run(self, request: ProcessRequest) -> ProcessResult:
        self.requests.append(request)
        return self.result


@pytest.mark.asyncio
async def test_ffprobe_reads_mixed_case_metadata_tags(tmp_path: Path):
    path = tmp_path / "job-id" / "song.flac"
    path.parent.mkdir()
    path.write_bytes(b"audio")
    output = json.dumps(
        {
            "streams": [{"tags": {"Title": " ignored stream title "}}],
            "format": {
                "tags": {
                    "title": " Song ",
                    "ArTiSt": "Artist",
                    "ALBUM": " Album ",
                    "album_artist": "Album Artist",
                    "Composer": "Composer",
                    "genre": "Rap/Hip Hop",
                    "LYRICS": " line one\nline two ",
                    "COMMENT": "not exposed",
                }
            },
        }
    )
    runner = CapturingRunner(ProcessResult(exit_code=0, output=output))
    settings = Settings(music_process_timeout_seconds=77)
    reader = FfprobeMetadataReader(settings, runner)

    metadata = await reader.read(path)

    assert metadata == MusicMetadata(
        title="Song",
        artist="Artist",
        album="Album",
        album_artist="Album Artist",
        composer="Composer",
        genre="Rap/Hip Hop",
        lyrics="line one\nline two",
    )
    assert runner.requests == [
        ProcessRequest(
            argv=(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ),
            timeout_seconds=77,
            label="ffprobe",
            track_id="unknown",
            job_id="job-id",
        )
    ]


@pytest.mark.asyncio
async def test_ffprobe_turns_blank_values_into_none(tmp_path: Path):
    path = tmp_path / "job" / "song.flac"
    runner = CapturingRunner(
        ProcessResult(
            exit_code=0,
            output=json.dumps({"format": {"tags": {"TITLE": "   "}}}),
        )
    )
    reader = FfprobeMetadataReader(Settings(), runner)

    assert await reader.read(path) == MusicMetadata()


@pytest.mark.asyncio
async def test_ffprobe_rejects_non_zero_exit(tmp_path: Path):
    path = tmp_path / "job" / "song.flac"
    runner = CapturingRunner(ProcessResult(exit_code=2, output="probe failed"))
    reader = FfprobeMetadataReader(Settings(), runner)

    with pytest.raises(RuntimeError, match="exit code 2"):
        await reader.read(path)
