from __future__ import annotations

import asyncio
import hashlib
import shutil
import stat
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from app.models.music_artifact import MusicArtifactResponse, MusicMetadata


_HASH_CHUNK_SIZE = 1024 * 1024


class ArtifactRecord(MusicArtifactResponse):
    file_path: Path = Field(exclude=True, repr=False)


def require_beneath_root(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("Artifact path escapes configured root")
    return resolved


def _inspect_and_hash_file(path: Path, maximum_size_bytes: int) -> tuple[int, str]:
    file_stat = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("Artifact must be a regular file")
    if file_stat.st_size == 0:
        raise ValueError("Artifact must not be empty")
    if file_stat.st_size > maximum_size_bytes:
        raise ValueError("Artifact exceeds configured size limit")

    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > maximum_size_bytes:
                raise ValueError("Artifact exceeds configured size limit")
            digest.update(chunk)

    if size_bytes == 0:
        raise ValueError("Artifact must not be empty")
    if size_bytes != file_stat.st_size:
        raise ValueError("Artifact changed during registration")
    return size_bytes, digest.hexdigest()


def _validated_file_size(path: Path) -> int:
    file_stat = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("Artifact must be a regular file")
    return file_stat.st_size


def _remove_directory(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass


class ArtifactStore:
    def __init__(
        self,
        artifact_root: Path,
        maximum_size_bytes: int,
        ttl_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        artifact_root.mkdir(parents=True, exist_ok=True)
        self._root = artifact_root.resolve(strict=True)
        self._maximum_size_bytes = maximum_size_bytes
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._records: dict[str, ArtifactRecord] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        track_id: str,
        file_path: Path,
        metadata: MusicMetadata,
    ) -> ArtifactRecord:
        candidate = Path(file_path)
        if candidate.is_symlink():
            raise ValueError("Artifact must not be a symlink")
        resolved = require_beneath_root(self._root, candidate)
        if resolved.parent == self._root:
            raise ValueError("Artifact must be stored in a job directory")

        try:
            size_bytes, sha256 = await asyncio.to_thread(
                _inspect_and_hash_file,
                resolved,
                self._maximum_size_bytes,
            )
        except (FileNotFoundError, OSError) as error:
            raise ValueError("Artifact must be a readable regular file") from error

        record = ArtifactRecord(
            artifact_id=str(uuid4()),
            track_id=track_id,
            filename=resolved.name,
            size_bytes=size_bytes,
            sha256=sha256,
            expires_at=self._clock() + timedelta(seconds=self._ttl_seconds),
            metadata=metadata,
            file_path=resolved,
        )
        async with self._lock:
            self._records[record.artifact_id] = record
        return record

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        async with self._lock:
            record = self._records.get(str(artifact_id))
        if record is None:
            return None
        if record.expires_at <= self._clock():
            await self.delete(record.artifact_id)
            return None

        if record.file_path.is_symlink():
            raise ValueError("Artifact must not be a symlink")
        resolved = require_beneath_root(self._root, record.file_path)
        try:
            size_bytes = await asyncio.to_thread(_validated_file_size, resolved)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError("Artifact must be a readable regular file") from error
        if size_bytes != record.size_bytes:
            return None
        return record

    async def delete(self, artifact_id: str) -> bool:
        async with self._lock:
            record = self._records.get(str(artifact_id))
        if record is None:
            return False

        require_beneath_root(self._root, record.file_path)
        job_directory = record.file_path.parent
        if job_directory.is_symlink():
            raise ValueError("Artifact path escapes configured root")
        resolved_job_directory = require_beneath_root(self._root, job_directory)
        if resolved_job_directory == self._root:
            raise ValueError("Refusing to delete artifact root")

        await asyncio.to_thread(_remove_directory, resolved_job_directory)
        async with self._lock:
            if self._records.get(record.artifact_id) is record:
                del self._records[record.artifact_id]
        return True

    async def sweep_expired(self) -> int:
        now = self._clock()
        async with self._lock:
            expired_ids = [
                artifact_id
                for artifact_id, record in self._records.items()
                if record.expires_at <= now
            ]

        deleted = 0
        for artifact_id in expired_ids:
            deleted += await self.delete(artifact_id)
        return deleted

    async def remove_stale_directories(self) -> int:
        async with self._lock:
            active_directories = {
                record.file_path.parent for record in self._records.values()
            }

        removed = 0
        for candidate in list(self._root.iterdir()):
            if candidate in active_directories or candidate.is_symlink():
                continue
            if not candidate.is_dir():
                continue
            resolved = require_beneath_root(self._root, candidate)
            if resolved == self._root:
                continue
            await asyncio.to_thread(_remove_directory, resolved)
            removed += 1
        return removed
