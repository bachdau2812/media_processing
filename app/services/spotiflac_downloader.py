from __future__ import annotations

import asyncio
import os
import re
import stat
import sys
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.config import Settings
from app.services.process_runner import ProcessRequest, ProcessResult


_TRACK_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_SPOTIFY_TRACK_PREFIX = "https://open.spotify.com/track/"
_DEFAULT_MONITOR_INTERVAL_SECONDS = 0.1


class ProcessRunnerLike(Protocol):
    async def run(self, request: ProcessRequest) -> ProcessResult: ...


class SpotiFlacDownloader:
    def __init__(
        self,
        settings: Settings,
        runner: ProcessRunnerLike,
        *,
        launcher: Path | None = None,
        monitor_interval_seconds: float = _DEFAULT_MONITOR_INTERVAL_SECONDS,
    ) -> None:
        if monitor_interval_seconds <= 0:
            raise ValueError("Monitor interval must be greater than zero")
        self._settings = settings
        self._runner = runner
        self._launcher = launcher or (
            Path(__file__).parents[2] / "spotiflac" / "native_no_browser.py"
        )
        self._monitor_interval_seconds = monitor_interval_seconds

    async def download(self, track_id: str, job_dir: Path) -> Path:
        if not _TRACK_ID.fullmatch(track_id or ""):
            raise ValueError(
                "Spotify trackId must contain 22 base-62 characters"
            )
        job_directory = await asyncio.to_thread(
            self._create_job_directory, Path(job_dir)
        )
        request = ProcessRequest(
            argv=(
                sys.executable,
                str(self._launcher),
                _SPOTIFY_TRACK_PREFIX + track_id,
                str(job_directory),
                "--service",
                "deezer",
                "qobuz",
                "tidal",
                "--no-extensions-fallback",
                "--quality",
                "LOSSLESS",
                "--retries",
                "2",
                "--timeout",
                "180",
                "--verbose",
            ),
            timeout_seconds=self._settings.music_process_timeout_seconds,
            label="SpotiFLAC",
            track_id=track_id,
            job_id=job_directory.name,
        )

        process_task = asyncio.create_task(self._runner.run(request))
        try:
            while not process_task.done():
                done, _ = await asyncio.wait(
                    {process_task}, timeout=self._monitor_interval_seconds
                )
                if done:
                    break
                size_bytes = await asyncio.to_thread(
                    _total_regular_file_bytes, job_directory
                )
                if size_bytes > self._settings.music_artifact_max_size:
                    raise ValueError(
                        "Downloaded artifact exceeds configured size limit"
                    )

            result = await process_task
            if result.exit_code != 0:
                raise RuntimeError(
                    f"SpotiFLAC failed with exit code {result.exit_code}: "
                    f"{result.output}"
                )
            return await asyncio.to_thread(
                self._validate_download, job_directory
            )
        finally:
            if not process_task.done():
                process_task.cancel()
                with suppress(asyncio.CancelledError):
                    await process_task

    def _create_job_directory(self, job_directory: Path) -> Path:
        try:
            UUID(job_directory.name)
        except (ValueError, AttributeError) as error:
            raise ValueError("SpotiFLAC job directory name must be a UUID") from error

        root = self._settings.artifact_root.resolve(strict=True)
        candidate = Path(os.path.abspath(job_directory))
        if candidate.parent.resolve(strict=True) != root:
            raise ValueError("SpotiFLAC job directory must be under artifact root")
        candidate.mkdir(parents=False, exist_ok=False)
        return candidate

    def _validate_download(self, job_directory: Path) -> Path:
        if _total_regular_file_bytes(job_directory) > (
            self._settings.music_artifact_max_size
        ):
            raise ValueError("Downloaded artifact exceeds configured size limit")
        flac_files = _find_flac_entries(job_directory)
        if len(flac_files) != 1:
            raise RuntimeError(
                "SpotiFLAC must produce exactly one FLAC file, "
                f"found {len(flac_files)}"
            )
        flac_file = flac_files[0]
        file_stat = flac_file.lstat()
        if stat.S_ISLNK(file_stat.st_mode):
            raise ValueError("SpotiFLAC produced a symlink FLAC file")
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("SpotiFLAC output must be a regular FLAC file")
        if file_stat.st_size == 0:
            raise ValueError("SpotiFLAC produced an empty FLAC file")
        return flac_file


def _walk_files(root: Path):
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        current = Path(current_root)
        safe_directories = []
        for directory in directories:
            candidate = current / directory
            try:
                if not stat.S_ISLNK(candidate.lstat().st_mode):
                    safe_directories.append(directory)
            except FileNotFoundError:
                continue
        directories[:] = safe_directories
        for filename in filenames:
            yield current / filename


def _total_regular_file_bytes(root: Path) -> int:
    total = 0
    for candidate in _walk_files(root):
        try:
            file_stat = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(file_stat.st_mode):
            total += file_stat.st_size
    return total


def _find_flac_entries(root: Path) -> list[Path]:
    return [
        candidate
        for candidate in _walk_files(root)
        if candidate.name.lower().endswith(".flac")
    ]
