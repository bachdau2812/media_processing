import sys

import pytest

from app.services.process_runner import ProcessRequest, ProcessRunner


def request_for(*argv: str, timeout_seconds: int = 5) -> ProcessRequest:
    return ProcessRequest(
        argv=tuple(argv),
        timeout_seconds=timeout_seconds,
        label="test-process",
        track_id="1234567890123456789012",
        job_id="00000000-0000-0000-0000-000000000001",
    )


@pytest.mark.asyncio
async def test_process_runner_returns_non_zero_exit_and_merged_output():
    runner = ProcessRunner()

    result = await runner.run(
        request_for(
            sys.executable,
            "-c",
            "import sys; print('stdout'); print('stderr', file=sys.stderr); sys.exit(7)",
        )
    )

    assert result.exit_code == 7
    assert "stdout" in result.output
    assert "stderr" in result.output


@pytest.mark.asyncio
async def test_process_runner_sets_utf8_environment():
    runner = ProcessRunner()

    result = await runner.run(
        request_for(
            sys.executable,
            "-c",
            (
                "import os; "
                "print(os.environ.get('PYTHONUTF8')); "
                "print(os.environ.get('PYTHONIOENCODING'))"
            ),
        )
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == ["1", "utf-8"]


@pytest.mark.asyncio
async def test_process_runner_times_out_and_terminates_process():
    runner = ProcessRunner()

    with pytest.raises(TimeoutError, match="timed out"):
        await runner.run(
            request_for(
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
                timeout_seconds=1,
            )
        )


@pytest.mark.asyncio
async def test_process_runner_caps_output_bytes():
    runner = ProcessRunner(max_output_bytes=64)

    result = await runner.run(
        request_for(sys.executable, "-c", "print('x' * 4096)")
    )

    assert result.exit_code == 0
    assert len(result.output.encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_process_runner_redacts_tokens_cookies_and_authorization_headers():
    runner = ProcessRunner()
    secrets = (
        "access token acquired: access-secret\n"
        "Cookie: cookie-secret\n"
        "Set-Cookie: session-secret\n"
        "Authorization: Bearer authorization-secret\n"
        "(b'authorization', b'tuple-secret')\n"
    )

    result = await runner.run(
        request_for(sys.executable, "-c", f"print({secrets!r})")
    )

    assert result.exit_code == 0
    assert result.output.count("[REDACTED]") == 5
    for secret in (
        "access-secret",
        "cookie-secret",
        "session-secret",
        "authorization-secret",
        "tuple-secret",
    ):
        assert secret not in result.output
