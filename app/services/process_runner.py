from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass

from app.logging_utils import sanitize_log_text


_DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 8192

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    output: str


@dataclass(frozen=True)
class ProcessRequest:
    argv: tuple[str, ...]
    timeout_seconds: int
    label: str
    track_id: str
    job_id: str


class ProcessRunner:
    def __init__(self, max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        if max_output_bytes <= 0:
            raise ValueError("Maximum captured output must be greater than zero")
        self._max_output_bytes = max_output_bytes

    async def run(self, request: ProcessRequest) -> ProcessResult:
        self._validate(request)
        started_at = time.monotonic()
        logger.info(
            "music_process_started label=%s track_id=%s job_id=%s "
            "timeout_seconds=%s",
            request.label,
            request.track_id,
            request.job_id,
            request.timeout_seconds,
        )
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        process_options: dict[str, object] = {}
        if os.name == "posix":
            process_options["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(
            *request.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=environment,
            **process_options,
        )
        output_task = asyncio.create_task(self._read_output(process))
        try:
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=request.timeout_seconds
                )
            except TimeoutError as error:
                await self._terminate(process)
                output = await output_task
                logger.warning(
                    "music_process_timed_out label=%s track_id=%s job_id=%s "
                    "elapsed_ms=%s output=%r",
                    request.label,
                    request.track_id,
                    request.job_id,
                    _elapsed_ms(started_at),
                    sanitize_log_text(output, self._max_output_bytes),
                )
                raise TimeoutError(
                    f"{request.label} timed out after "
                    f"{request.timeout_seconds} seconds"
                ) from error
            except asyncio.CancelledError:
                await self._terminate(process)
                await output_task
                logger.info(
                    "music_process_cancelled label=%s track_id=%s job_id=%s "
                    "elapsed_ms=%s",
                    request.label,
                    request.track_id,
                    request.job_id,
                    _elapsed_ms(started_at),
                )
                raise

            output = await output_task
            sanitized_output = sanitize_log_text(
                output, self._max_output_bytes
            )
            if process.returncode == 0:
                logger.info(
                    "music_process_completed label=%s track_id=%s job_id=%s "
                    "exit_code=%s elapsed_ms=%s",
                    request.label,
                    request.track_id,
                    request.job_id,
                    process.returncode,
                    _elapsed_ms(started_at),
                )
            else:
                logger.warning(
                    "music_process_failed label=%s track_id=%s job_id=%s "
                    "exit_code=%s elapsed_ms=%s output=%r",
                    request.label,
                    request.track_id,
                    request.job_id,
                    process.returncode,
                    _elapsed_ms(started_at),
                    sanitized_output,
                )
            return ProcessResult(
                exit_code=process.returncode,
                output=sanitized_output,
            )
        finally:
            if process.returncode is None:
                await self._terminate(process)
            if not output_task.done():
                output_task.cancel()

    async def _read_output(self, process: asyncio.subprocess.Process) -> bytes:
        if process.stdout is None:
            return b""
        captured = bytearray()
        while chunk := await process.stdout.read(_READ_CHUNK_BYTES):
            remaining = self._max_output_bytes - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
        return bytes(captured)

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        self._signal_process(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
            return
        except TimeoutError:
            pass
        self._signal_process(process, signal.SIGKILL)
        await process.wait()

    def _signal_process(
        self, process: asyncio.subprocess.Process, requested_signal: signal.Signals
    ) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, requested_signal)
            elif requested_signal == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass

    def _validate(self, request: ProcessRequest) -> None:
        if not request.argv or any(not argument for argument in request.argv):
            raise ValueError("CLI command and arguments are required")
        if request.timeout_seconds <= 0:
            raise ValueError("CLI timeout must be greater than zero")
        if not request.label or not request.track_id or not request.job_id:
            raise ValueError("CLI logging context is required")


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))
