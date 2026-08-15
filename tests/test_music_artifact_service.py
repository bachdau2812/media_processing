import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.models.music_artifact import MusicMetadata
from app.services.music_artifact_service import (
    MusicArtifactService,
    MusicCapacityExceeded,
)


TRACK_ID = "1234567890123456789012"


def symlink_directory_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symbolic links")
        raise


class FakeDownloader:
    def __init__(self, events: list[str], content: bytes = b"flac") -> None:
        self.events = events
        self.content = content

    async def download(self, track_id: str, job_dir: Path) -> Path:
        self.events.append("download")
        job_dir.mkdir()
        audio = job_dir / "song.flac"
        audio.write_bytes(self.content)
        return audio


class FakeMetadataReader:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error

    async def read(self, path: Path) -> MusicMetadata:
        self.events.append("metadata")
        if self.error:
            raise self.error
        return MusicMetadata(genre="Rock")


class FakeArtifactStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def register(
        self, track_id: str, file_path: Path, metadata: MusicMetadata
    ):
        from datetime import UTC, datetime
        from app.services.artifact_store import ArtifactRecord

        self.events.append("register")
        return ArtifactRecord(
            artifact_id="293c4b79-aa20-4d0b-a1b7-2d0a201a45b4",
            track_id=track_id,
            filename=file_path.name,
            size_bytes=file_path.stat().st_size,
            sha256="hash",
            expires_at=datetime(2026, 8, 15, tzinfo=UTC),
            metadata=metadata,
            file_path=file_path,
        )


def settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "artifact_root": tmp_path / "artifacts",
        "music_capacity_wait_seconds": 1,
        "music_max_concurrent_downloads": 1,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_create_downloads_reads_metadata_then_registers(tmp_path: Path):
    events: list[str] = []
    service = MusicArtifactService(
        settings(tmp_path),
        FakeDownloader(events),
        FakeMetadataReader(events),
        FakeArtifactStore(events),
    )

    response = await service.create(TRACK_ID)

    assert events == ["download", "metadata", "register"]
    assert response.track_id == TRACK_ID
    assert response.metadata.genre == "Rock"
    assert response.size_bytes == len(b"flac")


@pytest.mark.asyncio
async def test_failed_metadata_removes_only_unregistered_uuid_job(
    tmp_path: Path,
):
    events: list[str] = []
    artifact_root = tmp_path / "artifacts"
    protected = artifact_root / "protected"
    protected.mkdir(parents=True)
    (protected / "keep.txt").write_text("keep")
    service = MusicArtifactService(
        settings(tmp_path),
        FakeDownloader(events),
        FakeMetadataReader(events, RuntimeError("raw CLI secret")),
        FakeArtifactStore(events),
    )

    with pytest.raises(RuntimeError, match="raw CLI secret"):
        await service.create(TRACK_ID)

    assert (protected / "keep.txt").read_text() == "keep"
    assert [entry.name for entry in artifact_root.iterdir()] == ["protected"]
    assert events == ["download", "metadata"]


@pytest.mark.asyncio
async def test_failed_metadata_cleans_uuid_job_through_symlinked_root(
    tmp_path: Path,
):
    real_root = tmp_path / "real-artifacts"
    real_root.mkdir()
    linked_root = tmp_path / "linked-artifacts"
    symlink_directory_or_skip(linked_root, real_root)
    events: list[str] = []
    service = MusicArtifactService(
        settings(tmp_path, artifact_root=linked_root),
        FakeDownloader(events),
        FakeMetadataReader(events, RuntimeError("metadata failed")),
        FakeArtifactStore(events),
    )

    with pytest.raises(RuntimeError, match="metadata failed"):
        await service.create(TRACK_ID)

    assert list(real_root.iterdir()) == []
    assert events == ["download", "metadata"]


@pytest.mark.asyncio
async def test_capacity_wait_is_bounded(tmp_path: Path):
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingDownloader(FakeDownloader):
        async def download(self, track_id: str, job_dir: Path) -> Path:
            started.set()
            await release.wait()
            return await super().download(track_id, job_dir)

    config = settings(tmp_path)
    events: list[str] = []
    service = MusicArtifactService(
        config,
        BlockingDownloader(events),
        FakeMetadataReader(events),
        FakeArtifactStore(events),
    )
    first = asyncio.create_task(service.create(TRACK_ID))
    await started.wait()

    with pytest.raises(MusicCapacityExceeded):
        await service.create(TRACK_ID)

    release.set()
    await first
