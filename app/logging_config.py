from __future__ import annotations

import gzip
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import shutil
import sys
import time

from app.config import Settings


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
LOG_MAX_BYTES = 100 * 1024 * 1024
LOG_BACKUP_COUNT = 30
LOG_RETENTION_DAYS = 14
_MANAGED_ATTRIBUTE = "_sensitive_checker_managed"


class CombinedRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(
        self,
        filename: str | Path,
        *,
        max_bytes: int = LOG_MAX_BYTES,
        backup_count: int = LOG_BACKUP_COUNT,
        retention_days: int = LOG_RETENTION_DAYS,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        if backup_count <= 0:
            raise ValueError("backup_count must be greater than zero")
        if retention_days <= 0:
            raise ValueError("retention_days must be greater than zero")
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        super().__init__(
            str(filename),
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
            utc=False,
        )

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if super().shouldRollover(record):
            return True
        message = f"{self.format(record)}{self.terminator}"
        encoded_size = len(message.encode(self.encoding or "utf-8"))
        try:
            active_size = Path(self.baseFilename).stat().st_size
        except FileNotFoundError:
            active_size = 0
        return active_size + encoded_size > self.max_bytes

    def doRollover(self) -> None:
        current_time = int(time.time())
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        archive = self._next_archive_path(current_time)
        try:
            if Path(self.baseFilename).exists():
                self._gzip_rotate(Path(self.baseFilename), archive)
            self.cleanup_archives(now=current_time)
        finally:
            if not self.delay:
                self.stream = self._open()
            self.rolloverAt = self.computeRollover(current_time)
            while self.rolloverAt <= current_time:
                self.rolloverAt += self.interval

    def cleanup_archives(self, *, now: float | None = None) -> None:
        current_time = time.time() if now is None else now
        cutoff = current_time - (self.retention_days * 24 * 60 * 60)
        archives = self._archive_paths()
        for archive in archives:
            try:
                if archive.stat().st_mtime < cutoff:
                    archive.unlink()
            except FileNotFoundError:
                continue
        remaining = [archive for archive in archives if archive.exists()]
        remaining.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for archive in remaining[self.backupCount :]:
            archive.unlink(missing_ok=True)

    def _archive_paths(self) -> list[Path]:
        active = Path(self.baseFilename)
        return list(active.parent.glob(f"{active.name}.*.gz"))

    def _next_archive_path(self, timestamp: int) -> Path:
        active = Path(self.baseFilename)
        suffix = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(timestamp))
        candidate = active.with_name(f"{active.name}.{suffix}.gz")
        sequence = 1
        while candidate.exists():
            candidate = active.with_name(
                f"{active.name}.{suffix}.{sequence}.gz"
            )
            sequence += 1
        return candidate

    @staticmethod
    def _gzip_rotate(source: Path, destination: Path) -> None:
        temporary = destination.with_name(f"{destination.name}.tmp")
        try:
            with source.open("rb") as input_stream:
                with gzip.open(temporary, "wb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream)
            os.replace(temporary, destination)
            source.unlink()
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def configure_logging(settings: Settings | None = None) -> None:
    resolved_settings = settings or Settings()
    log_dir = resolved_settings.log_dir.expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    file_handler = CombinedRotatingFileHandler(
        log_dir / f"log-{resolved_settings.service_port}.log"
    )
    file_handler.setFormatter(formatter)
    new_handlers: tuple[logging.Handler, ...] = (
        console_handler,
        file_handler,
    )
    for handler in new_handlers:
        setattr(handler, _MANAGED_ATTRIBUTE, True)

    root_logger = logging.getLogger()
    for handler in tuple(root_logger.handlers):
        if getattr(handler, _MANAGED_ATTRIBUTE, False):
            root_logger.removeHandler(handler)
            handler.close()
    for handler in new_handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    logging.getLogger("app").setLevel(logging.INFO)
