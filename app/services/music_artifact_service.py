from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from app.config import Settings
from app.models.music_artifact import MusicArtifactResponse, MusicMetadata


logger = logging.getLogger(__name__)


class DownloaderLike(Protocol):
    async def download(self, track_id: str, job_dir: Path) -> Path: ...


class MetadataReaderLike(Protocol):
    async def read(self, path: Path) -> MusicMetadata: ...


class ArtifactStoreLike(Protocol):
    async def register(
        self, track_id: str, file_path: Path, metadata: MusicMetadata
    ) -> MusicArtifactResponse: ...


class MusicCapacityExceeded(Exception):
    pass


class MusicProviderFailed(RuntimeError):
    pass


class MusicArtifactTooLarge(ValueError):
    pass


class MusicFetchTimeout(TimeoutError):
    pass


class MusicArtifactService:
    def __init__(
        self,
        settings: Settings,
        downloader: DownloaderLike,
        metadata_reader: MetadataReaderLike,
        artifact_store: ArtifactStoreLike,
    ) -> None:
        self._root = Path(settings.artifact_root).absolute()
        self._downloader = downloader
        self._metadata_reader = metadata_reader
        self._artifact_store = artifact_store
        self._capacity_wait_seconds = float(
            settings.music_capacity_wait_seconds
        )
        self._capacity = asyncio.Semaphore(
            settings.music_max_concurrent_downloads
        )

    async def create(self, track_id: str) -> MusicArtifactResponse:
        try:
            await asyncio.wait_for(
                self._capacity.acquire(),
                timeout=self._capacity_wait_seconds,
            )
        except TimeoutError as error:
            raise MusicCapacityExceeded from error

        try:
            await asyncio.to_thread(
                self._root.mkdir, parents=True, exist_ok=True
            )
            job_directory = self._root / str(uuid4())
            try:
                audio_path = await self._downloader.download(
                    track_id, job_directory
                )
                metadata = await self._metadata_reader.read(audio_path)
                return await self._artifact_store.register(
                    track_id, audio_path, metadata
                )
            except BaseException as error:
                try:
                    await asyncio.to_thread(
                        _remove_guarded_uuid_directory,
                        self._root,
                        job_directory,
                    )
                except Exception:
                    logger.exception(
                        "Failed to clean unregistered music job %s",
                        job_directory.name,
                    )
                if isinstance(error, TimeoutError):
                    raise MusicFetchTimeout(str(error)) from error
                if isinstance(error, ValueError) and _is_too_large(error):
                    raise MusicArtifactTooLarge(str(error)) from error
                if isinstance(error, (RuntimeError, ValueError)):
                    raise MusicProviderFailed(str(error)) from error
                raise
        finally:
            self._capacity.release()


def _is_too_large(error: ValueError) -> bool:
    message = str(error).lower()
    return "exceed" in message and "size" in message


def _remove_guarded_uuid_directory(root: Path, candidate: Path) -> None:
    root_resolved = root.resolve(strict=True)
    absolute_candidate = Path(os.path.abspath(candidate))
    if absolute_candidate.parent != root_resolved:
        raise ValueError("Music job directory must be directly beneath root")
    UUID(absolute_candidate.name)

    try:
        candidate_stat = absolute_candidate.lstat()
    except FileNotFoundError:
        return
    file_attributes = getattr(candidate_stat, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(candidate_stat.st_mode) or bool(
        file_attributes & reparse_attribute
    ):
        raise ValueError("Refusing to clean a linked music job directory")
    if not stat.S_ISDIR(candidate_stat.st_mode):
        raise ValueError("Music job path is not a directory")
    shutil.rmtree(absolute_candidate)
