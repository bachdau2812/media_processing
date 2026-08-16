from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_music_defaults_are_bounded(tmp_path: Path):
    settings = Settings(artifact_root=tmp_path)

    assert settings.music_artifact_max_size == 100 * 1024 * 1024
    assert settings.music_max_concurrent_downloads == 2
    assert settings.service_host == "127.0.0.1"
    assert settings.service_port == 8000
    assert settings.log_dir == Path("logs")


def test_rejects_unknown_compute_device(tmp_path: Path):
    with pytest.raises(ValidationError):
        Settings(artifact_root=tmp_path, compute_device="metal")


def test_log_directory_can_be_overridden_from_environment(
    tmp_path: Path,
    monkeypatch,
):
    expected = tmp_path / "service-logs"
    monkeypatch.setenv("LOG_DIR", str(expected))

    settings = Settings(_env_file=None)

    assert settings.log_dir == expected
