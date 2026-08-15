import hashlib
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.models.music_artifact import MusicMetadata
from app.services.artifact_store import ArtifactStore


TRACK_ID = "1234567890123456789012"


def symlink_or_skip(
    link: Path, target: Path, *, target_is_directory: bool = False
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symbolic links")
        raise


@pytest.fixture
def clock():
    now = datetime(2026, 8, 15, tzinfo=UTC)

    def current_time():
        return now

    def advance(seconds: int):
        nonlocal now
        now += timedelta(seconds=seconds)

    current_time.advance = advance
    return current_time


@pytest.mark.asyncio
async def test_registers_file_with_hash_and_expiry(tmp_path: Path, clock):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    audio = job / "song.flac"
    audio.write_bytes(b"audio")
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)

    record = await store.register(TRACK_ID, audio, MusicMetadata(title="Song"))

    assert UUID(record.artifact_id)
    assert record.size_bytes == 5
    assert record.sha256 == hashlib.sha256(b"audio").hexdigest()
    assert record.expires_at == datetime(2026, 8, 15, 0, 15, tzinfo=UTC)
    assert await store.get(record.artifact_id) == record


@pytest.mark.asyncio
async def test_register_rejects_empty_oversized_and_non_regular_files(
    tmp_path: Path, clock
):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    store = ArtifactStore(root, 5, 900, clock)

    empty = job / "empty.flac"
    empty.touch()
    oversized = job / "oversized.flac"
    oversized.write_bytes(b"123456")
    directory = job / "not-a-file"
    directory.mkdir()
    for path in (empty, oversized, directory):
        with pytest.raises(ValueError):
            await store.register(TRACK_ID, path, MusicMetadata())


@pytest.mark.asyncio
async def test_register_rejects_symlink(tmp_path: Path, clock):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    real_audio = job / "real.flac"
    real_audio.write_bytes(b"audio")
    link = job / "link.flac"
    symlink_or_skip(link, real_audio)
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)

    with pytest.raises(ValueError):
        await store.register(TRACK_ID, link, MusicMetadata())


@pytest.mark.asyncio
async def test_register_rejects_symlinked_job_directory_inside_root(
    tmp_path: Path, clock
):
    root = tmp_path / "artifacts"
    real_job = root / "real-job"
    real_job.mkdir(parents=True)
    audio = real_job / "song.flac"
    audio.write_bytes(b"audio")
    linked_job = root / "link-job"
    symlink_or_skip(linked_job, real_job, target_is_directory=True)
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)

    with pytest.raises(ValueError, match="symlink"):
        await store.register(TRACK_ID, linked_job / "song.flac", MusicMetadata())


@pytest.mark.asyncio
async def test_register_refuses_path_outside_artifact_root(tmp_path: Path, clock):
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"audio")
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)

    with pytest.raises(ValueError, match="escapes configured root"):
        await store.register(TRACK_ID, outside, MusicMetadata())


@pytest.mark.asyncio
async def test_delete_removes_job_directory_and_is_idempotent(tmp_path: Path, clock):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    audio = job / "song.flac"
    audio.write_bytes(b"audio")
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)
    record = await store.register(TRACK_ID, audio, MusicMetadata())

    await store.delete(record.artifact_id)
    await store.delete(record.artifact_id)

    assert not job.exists()
    assert await store.get(record.artifact_id) is None


@pytest.mark.asyncio
async def test_delete_refuses_job_directory_replaced_with_outside_symlink(
    tmp_path: Path, clock
):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    audio = job / "song.flac"
    audio.write_bytes(b"audio")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected_file = outside / "protected.txt"
    protected_file.write_text("do not remove")
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)
    record = await store.register(TRACK_ID, audio, MusicMetadata())
    audio.unlink()
    job.rmdir()
    symlink_or_skip(job, outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        await store.delete(record.artifact_id)

    assert protected_file.read_text() == "do not remove"
    with pytest.raises(ValueError, match="symlink"):
        await store.get(record.artifact_id)


@pytest.mark.asyncio
async def test_get_and_delete_reject_job_directory_replaced_with_inside_symlink(
    tmp_path: Path, clock
):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    audio = job / "song.flac"
    audio.write_bytes(b"audio")
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)
    record = await store.register(TRACK_ID, audio, MusicMetadata())

    audio.unlink()
    job.rmdir()
    replacement = root / "replacement"
    replacement.mkdir()
    replacement_audio = replacement / "song.flac"
    replacement_audio.write_bytes(b"audio")
    symlink_or_skip(job, replacement, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        await store.get(record.artifact_id)
    with pytest.raises(ValueError, match="symlink"):
        await store.delete(record.artifact_id)
    assert replacement_audio.read_bytes() == b"audio"


@pytest.mark.asyncio
async def test_expired_records_are_unavailable_and_swept(tmp_path: Path, clock):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    audio = job / "song.flac"
    audio.write_bytes(b"audio")
    store = ArtifactStore(root, 100 * 1024 * 1024, 10, clock)
    record = await store.register(TRACK_ID, audio, MusicMetadata())
    clock.advance(10)

    assert await store.get(record.artifact_id) is None
    assert not job.exists()
    assert await store.sweep_expired() == 0


@pytest.mark.asyncio
async def test_sweep_expired_removes_all_expired_artifacts(tmp_path: Path, clock):
    root = tmp_path / "artifacts"
    store = ArtifactStore(root, 100 * 1024 * 1024, 10, clock)
    records = []
    for name in ("job-1", "job-2"):
        job = root / name
        job.mkdir(parents=True)
        audio = job / "song.flac"
        audio.write_bytes(b"audio")
        records.append(await store.register(TRACK_ID, audio, MusicMetadata()))
    clock.advance(11)

    assert await store.sweep_expired() == 2
    for record in records:
        assert await store.get(record.artifact_id) is None
    assert not any(root.iterdir())


@pytest.mark.asyncio
async def test_remove_stale_directories_removes_real_job_directories_only(
    tmp_path: Path, clock
):
    root = tmp_path / "artifacts"
    stale = root / "stale-job"
    stale.mkdir(parents=True)
    (stale / "song.flac").write_bytes(b"audio")
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)

    assert await store.remove_stale_directories() == 1
    assert not stale.exists()


@pytest.mark.asyncio
async def test_remove_stale_directories_leaves_outside_symlink(
    tmp_path: Path, clock
):
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    protected_file = outside / "protected.txt"
    protected_file.write_text("do not remove")
    symlink_or_skip(root / "outside-link", outside, target_is_directory=True)
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)

    assert await store.remove_stale_directories() == 0
    assert protected_file.read_text() == "do not remove"
    assert (root / "outside-link").is_symlink()


@pytest.mark.asyncio
async def test_filesystem_work_runs_off_event_loop(tmp_path: Path, clock, monkeypatch):
    root = tmp_path / "artifacts"
    job = root / "job-1"
    job.mkdir(parents=True)
    audio = job / "song.flac"
    audio.write_bytes(b"audio")
    stale = root / "stale-job"
    stale.mkdir()
    (stale / "old.flac").write_bytes(b"old")
    store = ArtifactStore(root, 100 * 1024 * 1024, 900, clock)
    event_loop_thread = threading.get_ident()

    with monkeypatch.context() as patcher:
        for method_name in (
            "is_dir",
            "is_symlink",
            "iterdir",
            "lstat",
            "open",
            "resolve",
            "stat",
        ):
            method = getattr(Path, method_name)

            def require_worker_thread(self, *args, _method=method, **kwargs):
                assert threading.get_ident() != event_loop_thread
                return _method(self, *args, **kwargs)

            patcher.setattr(Path, method_name, require_worker_thread)

        record = await store.register(TRACK_ID, audio, MusicMetadata())
        assert await store.get(record.artifact_id) == record
        assert await store.delete(record.artifact_id)
        assert await store.remove_stale_directories() == 1
