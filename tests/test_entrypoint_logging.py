import gzip
import io
import logging
import os
from pathlib import Path
import sys
import time

import pytest

from app import __main__ as entrypoint
from app.config import Settings
from app.logging_config import CombinedRotatingFileHandler


def managed_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_sensitive_checker_managed", False)
    ]


@pytest.fixture(autouse=True)
def cleanup_managed_handlers():
    yield
    root = logging.getLogger()
    for handler in managed_handlers():
        root.removeHandler(handler)
        handler.close()


def test_configure_logging_writes_info_to_console_and_port_file(
    tmp_path: Path,
    monkeypatch,
):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stderr", console)
    settings = Settings(log_dir=tmp_path, service_port=8123)

    entrypoint.configure_logging(settings)
    logging.getLogger("app.test").info("shared-log-message")
    for handler in managed_handlers():
        handler.flush()

    assert "shared-log-message" in console.getvalue()
    assert "shared-log-message" in (tmp_path / "log-8123.log").read_text(
        encoding="utf-8"
    )
    assert logging.getLogger("app").isEnabledFor(logging.INFO)


def test_configure_logging_is_idempotent(tmp_path: Path):
    settings = Settings(log_dir=tmp_path)

    entrypoint.configure_logging(settings)
    entrypoint.configure_logging(settings)

    handlers = managed_handlers()
    assert len(handlers) == 2
    assert sum(isinstance(item, logging.StreamHandler) for item in handlers) == 2
    assert sum(
        isinstance(item, CombinedRotatingFileHandler) for item in handlers
    ) == 1


def isolated_logger(handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(f"test.logging.{id(handler)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def test_combined_handler_rolls_by_size_and_gzips_archive(tmp_path: Path):
    active = tmp_path / "log-8000.log"
    handler = CombinedRotatingFileHandler(
        active,
        max_bytes=70,
        backup_count=30,
        retention_days=14,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = isolated_logger(handler)

    logger.info("a" * 50)
    logger.info("b" * 50)
    handler.close()

    [archive] = list(tmp_path.glob("log-8000.log.*.gz"))
    with gzip.open(archive, "rt", encoding="utf-8") as source:
        assert "a" * 50 in source.read()
    assert "b" * 50 in active.read_text(encoding="utf-8")


def test_combined_handler_rolls_when_time_is_due(tmp_path: Path):
    active = tmp_path / "log-8000.log"
    handler = CombinedRotatingFileHandler(
        active,
        max_bytes=1024,
        backup_count=30,
        retention_days=14,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = isolated_logger(handler)
    logger.info("before-midnight")
    handler.rolloverAt = int(time.time()) - 1

    logger.info("after-midnight")
    handler.close()

    assert list(tmp_path.glob("log-8000.log.*.gz"))
    assert "after-midnight" in active.read_text(encoding="utf-8")


def test_archive_cleanup_enforces_age_and_count_without_deleting_active(
    tmp_path: Path,
):
    active = tmp_path / "log-8000.log"
    handler = CombinedRotatingFileHandler(
        active,
        max_bytes=1024,
        backup_count=30,
        retention_days=14,
    )
    now = time.time()
    for index in range(35):
        archive = tmp_path / f"log-8000.log.archive-{index:02d}.gz"
        archive.write_bytes(b"archive")
        os.utime(archive, (now - index, now - index))
    expired = tmp_path / "log-8000.log.expired.gz"
    expired.write_bytes(b"expired")
    old = now - (15 * 24 * 60 * 60)
    os.utime(expired, (old, old))

    handler.cleanup_archives(now=now)
    handler.close()

    assert active.exists()
    assert not expired.exists()
    assert len(list(tmp_path.glob("log-8000.log.*.gz"))) == 30
