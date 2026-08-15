from __future__ import annotations

import asyncio
import hashlib
import os
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


def _require_safe_components(
    root: Path, candidate: Path, *, allow_missing: bool
) -> Path:
    absolute_candidate = Path(os.path.abspath(candidate))
    try:
        relative_candidate = absolute_candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("Artifact path escapes configured root") from error

    current = root
    for component in relative_candidate.parts:
        current /= component
        try:
            component_stat = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                break
            raise
        is_junction = getattr(current, "is_junction", lambda: False)()
        if stat.S_ISLNK(component_stat.st_mode) or is_junction:
            raise ValueError("Artifact path contains a symlink component")

    return require_beneath_root(root, absolute_candidate)


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


def _register_file(
    root: Path, candidate: Path, maximum_size_bytes: int
) -> tuple[Path, int, str]:
    resolved = _require_safe_components(root, candidate, allow_missing=False)
    if resolved.parent == root:
        raise ValueError("Artifact must be stored in a job directory")
    size_bytes, sha256 = _inspect_and_hash_file(resolved, maximum_size_bytes)
    return resolved, size_bytes, sha256


def _get_file_size(root: Path, candidate: Path) -> int:
    resolved = _require_safe_components(root, candidate, allow_missing=True)
    return _validated_file_size(resolved)


def _delete_artifact_directory(root: Path, candidate: Path) -> None:
    resolved = _require_safe_components(root, candidate, allow_missing=True)
    job_directory = resolved.parent
    if job_directory == root:
        raise ValueError("Refusing to delete artifact root")
    _remove_directory(job_directory)


def _remove_stale_job_directories(
    root: Path, active_directories: set[Path]
) -> int:
    removed = 0
    for candidate in list(root.iterdir()):
        if candidate in active_directories:
            continue
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError:
            continue
        is_junction = getattr(candidate, "is_junction", lambda: False)()
        if stat.S_ISLNK(candidate_stat.st_mode) or is_junction:
            continue
        if not stat.S_ISDIR(candidate_stat.st_mode):
            continue
        resolved = require_beneath_root(root, candidate)
        if resolved == root:
            continue
        _remove_directory(resolved)
        removed += 1
    return removed


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
        try:
            resolved, size_bytes, sha256 = await asyncio.to_thread(
                _register_file,
                self._root,
                candidate,
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

        try:
            size_bytes = await asyncio.to_thread(
                _get_file_size, self._root, record.file_path
            )
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

        await asyncio.to_thread(
            _delete_artifact_directory, self._root, record.file_path
        )
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
        return await asyncio.to_thread(
            _remove_stale_job_directories,
            self._root,
            active_directories,
        )
