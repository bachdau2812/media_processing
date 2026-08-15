import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.services.process_runner import ProcessRequest, ProcessResult
from app.services.spotiflac_downloader import SpotiFlacDownloader


TRACK_ID = "1234567890123456789012"


class CapturingRunner:
    def __init__(
        self,
        result: ProcessResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or ProcessResult(exit_code=0, output="ok")
        self.error = error
        self.requests: list[ProcessRequest] = []

    async def run(self, request: ProcessRequest) -> ProcessResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def settings_for(tmp_path: Path, *, maximum_size: int = 100 * 1024 * 1024):
    return Settings(
        artifact_root=tmp_path / "artifacts",
        music_artifact_max_size=maximum_size,
        music_process_timeout_seconds=300,
    )


def new_job_dir(settings: Settings) -> Path:
    settings.artifact_root.mkdir(parents=True, exist_ok=True)
    return settings.artifact_root / str(uuid4())


def test_native_launcher_preserves_supported_version_and_provider_policy():
    launcher = Path(__file__).parents[1] / "spotiflac" / "native_no_browser.py"

    source = launcher.read_text(encoding="utf-8")

    assert 'SUPPORTED_SPOTIFLAC_VERSION = "1.6.0"' in source
    assert "qobuz._COMMUNITY_APIS.clear()" in source
    assert "tidal._TIDAL_API_POST = _without_url" in source
    assert "solver.solve_with_callback = browser_disabled" in source


@pytest.mark.asyncio
async def test_download_runs_exact_spotiflac_command(tmp_path: Path):
    settings = settings_for(tmp_path)
    runner = CapturingRunner()
    launcher = tmp_path / "native_no_browser.py"
    launcher.write_text("# launcher")
    job_dir = new_job_dir(settings)
    captured_requests: list[ProcessRequest] = []

    async def produce_flac(request: ProcessRequest) -> ProcessResult:
        captured_requests.append(request)
        (job_dir / "Song.FLAC").write_bytes(b"audio")
        return ProcessResult(exit_code=0, output="ok")

    runner.run = produce_flac
    downloader = SpotiFlacDownloader(settings, runner, launcher=launcher)

    result = await downloader.download(TRACK_ID, job_dir)

    assert result == job_dir / "Song.FLAC"
    expected = [
        sys.executable,
        str(launcher),
        f"https://open.spotify.com/track/{TRACK_ID}",
        str(job_dir),
        "--service", "deezer", "qobuz", "tidal",
        "--no-extensions-fallback",
        "--quality", "LOSSLESS",
        "--retries", "2",
        "--timeout", "180",
        "--verbose",
    ]
    [captured_request] = captured_requests
    assert list(captured_request.argv) == expected
    assert captured_request.timeout_seconds == 300
    assert captured_request.label == "SpotiFLAC"
    assert captured_request.track_id == TRACK_ID
    assert captured_request.job_id == job_dir.name


@pytest.mark.asyncio
async def test_download_rejects_invalid_track_id_before_process_invocation(
    tmp_path: Path,
):
    settings = settings_for(tmp_path)
    runner = CapturingRunner()
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(ValueError, match="22"):
        await downloader.download("not-a-track", new_job_dir(settings))

    assert runner.requests == []


@pytest.mark.asyncio
async def test_download_requires_uuid_job_directory(tmp_path: Path):
    settings = settings_for(tmp_path)
    runner = CapturingRunner()
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(ValueError, match="UUID"):
        await downloader.download(TRACK_ID, settings.artifact_root / "not-a-uuid")

    assert runner.requests == []


@pytest.mark.asyncio
async def test_download_rejects_uuid_job_directory_outside_artifact_root(
    tmp_path: Path,
):
    settings = settings_for(tmp_path)
    settings.artifact_root.mkdir(parents=True)
    runner = CapturingRunner()
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(ValueError, match="artifact root"):
        await downloader.download(TRACK_ID, tmp_path / str(uuid4()))

    assert runner.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("flac_count", [0, 2])
async def test_download_requires_exactly_one_flac(tmp_path: Path, flac_count: int):
    settings = settings_for(tmp_path)
    job_dir = new_job_dir(settings)
    runner = CapturingRunner()

    async def run(_request: ProcessRequest) -> ProcessResult:
        for index in range(flac_count):
            (job_dir / f"song-{index}.flac").write_bytes(b"audio")
        return ProcessResult(exit_code=0, output="provider output")

    runner.run = run
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(RuntimeError, match="exactly one FLAC"):
        await downloader.download(TRACK_ID, job_dir)


@pytest.mark.asyncio
async def test_download_rejects_symlink_flac(tmp_path: Path):
    settings = settings_for(tmp_path)
    job_dir = new_job_dir(settings)
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"audio")
    runner = CapturingRunner()

    async def run(_request: ProcessRequest) -> ProcessResult:
        try:
            (job_dir / "song.flac").symlink_to(outside)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows account cannot create symbolic links")
            raise
        return ProcessResult(exit_code=0, output="ok")

    runner.run = run
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(ValueError, match="symlink"):
        await downloader.download(TRACK_ID, job_dir)


@pytest.mark.asyncio
async def test_download_rejects_empty_flac(tmp_path: Path):
    settings = settings_for(tmp_path)
    job_dir = new_job_dir(settings)
    runner = CapturingRunner()

    async def run(_request: ProcessRequest) -> ProcessResult:
        (job_dir / "song.flac").touch()
        return ProcessResult(exit_code=0, output="ok")

    runner.run = run
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(ValueError, match="empty"):
        await downloader.download(TRACK_ID, job_dir)


@pytest.mark.asyncio
async def test_download_cancels_process_when_total_regular_bytes_exceed_limit(
    tmp_path: Path,
):
    settings = settings_for(tmp_path, maximum_size=5)
    job_dir = new_job_dir(settings)

    class GrowingRunner:
        def __init__(self) -> None:
            self.cancelled = asyncio.Event()

        async def run(self, _request: ProcessRequest) -> ProcessResult:
            (job_dir / "song.flac").write_bytes(b"123456")
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    runner = GrowingRunner()
    downloader = SpotiFlacDownloader(
        settings,
        runner,
        launcher=tmp_path / "launcher.py",
        monitor_interval_seconds=0.01,
    )

    with pytest.raises(ValueError, match="size limit"):
        await asyncio.wait_for(downloader.download(TRACK_ID, job_dir), timeout=1)

    assert runner.cancelled.is_set()


@pytest.mark.asyncio
async def test_cancelling_download_cancels_process_runner(tmp_path: Path):
    settings = settings_for(tmp_path)
    job_dir = new_job_dir(settings)

    class BlockingRunner:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def run(self, _request: ProcessRequest) -> ProcessResult:
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    runner = BlockingRunner()
    downloader = SpotiFlacDownloader(
        settings,
        runner,
        launcher=tmp_path / "launcher.py",
        monitor_interval_seconds=0.01,
    )
    download_task = asyncio.create_task(downloader.download(TRACK_ID, job_dir))
    await runner.started.wait()

    download_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await download_task

    assert runner.cancelled.is_set()


@pytest.mark.asyncio
async def test_download_propagates_process_timeout(tmp_path: Path):
    settings = settings_for(tmp_path)
    runner = CapturingRunner(error=TimeoutError("process timed out"))
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(TimeoutError, match="timed out"):
        await downloader.download(TRACK_ID, new_job_dir(settings))


@pytest.mark.asyncio
async def test_download_rejects_non_zero_process_exit(tmp_path: Path):
    settings = settings_for(tmp_path)
    runner = CapturingRunner(
        result=ProcessResult(exit_code=3, output="all providers failed")
    )
    downloader = SpotiFlacDownloader(settings, runner, launcher=tmp_path / "launcher.py")

    with pytest.raises(RuntimeError, match="exit code 3"):
        await downloader.download(TRACK_ID, new_job_dir(settings))
