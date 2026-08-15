from __future__ import annotations

import asyncio
import os
import re
import signal
from dataclasses import dataclass


_DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 8192
_ACCESS_TOKEN = re.compile(
    r"(?i)(access\s+token\s+acquired\s*:\s*)\S+"
)
_SENSITIVE_HEADER = re.compile(
    r"(?im)^(set-cookie|cookie|authorization)\s*:\s*.*$"
)
_SENSITIVE_HEADER_TUPLE = re.compile(
    r"(?i)\(b?'(set-cookie|cookie|authorization)',\s*b?'[^']*'\)"
)


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
                await output_task
                raise TimeoutError(
                    f"{request.label} timed out after "
                    f"{request.timeout_seconds} seconds"
                ) from error
            except asyncio.CancelledError:
                await self._terminate(process)
                await output_task
                raise

            output = await output_task
            return ProcessResult(
                exit_code=process.returncode,
                output=self._sanitize_and_cap(output),
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

    def _sanitize_and_cap(self, output: bytes) -> str:
        decoded = output.decode("utf-8", errors="replace")
        sanitized = _ACCESS_TOKEN.sub(r"\1[REDACTED]", decoded)
        sanitized = _SENSITIVE_HEADER_TUPLE.sub(
            r"\1=[REDACTED]", sanitized
        )
        sanitized = _SENSITIVE_HEADER.sub(r"\1=[REDACTED]", sanitized)
        return sanitized.encode("utf-8")[: self._max_output_bytes].decode(
            "utf-8", errors="ignore"
        )

    def _validate(self, request: ProcessRequest) -> None:
        if not request.argv or any(not argument for argument in request.argv):
            raise ValueError("CLI command and arguments are required")
        if request.timeout_seconds <= 0:
            raise ValueError("CLI timeout must be greater than zero")
        if not request.label or not request.track_id or not request.job_id:
            raise ValueError("CLI logging context is required")
