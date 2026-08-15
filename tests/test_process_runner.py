import asyncio
import logging
import sys

import pytest

from app.services.process_runner import ProcessRequest, ProcessRunner


def request_for(
    *argv: str,
    timeout_seconds: int = 5,
    stream_output: bool = False,
) -> ProcessRequest:
    return ProcessRequest(
        argv=tuple(argv),
        timeout_seconds=timeout_seconds,
        label="test-process",
        track_id="1234567890123456789012",
        job_id="00000000-0000-0000-0000-000000000001",
        stream_output=stream_output,
    )


def test_process_requests_disable_streaming_by_default():
    request = request_for(sys.executable, "-c", "print('captured')")

    assert getattr(request, "stream_output", None) is False


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


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["authorization", "cookie"])
async def test_process_runner_redacts_unterminated_sensitive_tuple_at_cap(
    header: str,
):
    runner = ProcessRunner(max_output_bytes=64)
    payload = (
        "x" * 32
        + f"(b'{header}', b'boundary-secret-that-extends-beyond-capture')"
    )

    result = await runner.run(
        request_for(
            sys.executable,
            "-c",
            f"import sys; sys.stdout.write({payload!r})",
        )
    )

    assert result.exit_code == 0
    assert "boundary" not in result.output
    assert f"{header}=[REDACTED]" in result.output
    assert len(result.output.encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_process_runner_logs_safe_lifecycle_without_argv(caplog):
    runner = ProcessRunner()
    secret_argument = "argument-must-not-be-logged"

    with caplog.at_level(
        logging.INFO, logger="app.services.process_runner"
    ):
        result = await runner.run(
            request_for(
                sys.executable,
                "-c",
                "print('ok')",
                secret_argument,
            )
        )

    assert result.exit_code == 0
    assert "music_process_started" in caplog.text
    assert "music_process_completed" in caplog.text
    assert "label=test-process" in caplog.text
    assert "job_id=00000000-0000-0000-0000-000000000001" in caplog.text
    assert "exit_code=0" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert secret_argument not in caplog.text


@pytest.mark.asyncio
async def test_process_runner_logs_only_sanitized_output_on_failure(caplog):
    runner = ProcessRunner()
    payload = "Authorization: Bearer failure-secret"

    with caplog.at_level(
        logging.INFO, logger="app.services.process_runner"
    ):
        result = await runner.run(
            request_for(
                sys.executable,
                "-c",
                f"import sys; print({payload!r}); sys.exit(7)",
            )
        )

    assert result.exit_code == 7
    assert "music_process_failed" in caplog.text
    assert "authorization=[redacted]" in caplog.text.lower()
    assert "failure-secret" not in caplog.text


@pytest.mark.asyncio
async def test_stream_enabled_logs_cr_lf_records_before_process_finishes(
    caplog,
):
    runner = ProcessRunner()
    script = (
        "import sys,time; "
        "sys.stdout.write('first\\n'); sys.stdout.flush(); "
        "time.sleep(1); "
        "sys.stdout.write('second\\rthird\\nlast'); sys.stdout.flush()"
    )

    with caplog.at_level(
        logging.INFO, logger="app.services.process_runner"
    ):
        task = asyncio.create_task(
            runner.run(
                request_for(
                    sys.executable,
                    "-c",
                    script,
                    stream_output=True,
                )
            )
        )
        for _ in range(200):
            if "line='first'" in caplog.text:
                break
            await asyncio.sleep(0.01)
        assert "line='first'" in caplog.text
        assert not task.done()
        result = await task

    streamed = [
        record.getMessage()
        for record in caplog.records
        if "music_process_output" in record.getMessage()
    ]
    assert [message.rsplit("line=", 1)[1] for message in streamed] == [
        "'first'",
        "'second'",
        "'third'",
        "'last'",
    ]
    assert result.output.splitlines() == ["first", "second", "third", "last"]


@pytest.mark.asyncio
async def test_stream_disabled_does_not_log_process_output(caplog):
    runner = ProcessRunner()

    with caplog.at_level(
        logging.INFO, logger="app.services.process_runner"
    ):
        result = await runner.run(
            request_for(sys.executable, "-c", "print('captured-only')")
        )

    assert result.output.strip() == "captured-only"
    assert "music_process_output" not in caplog.text


@pytest.mark.asyncio
async def test_stream_output_redacts_credentials_and_never_logs_argv(caplog):
    lines = (
        "Authorization: Bearer authorization-secret\n"
        "(b'x-api-key', b'api-key-secret')\n"
        "https://provider.invalid/file?token=query-secret&format=flac\n"
        "access_token=named-token-secret\n"
        "(b'x-deezer-client-ip', b'42.116.192.62')\n"
        "fresh credentials (app_id=712109809)\n"
    )
    argv_secret = "argv-secret"
    runner = ProcessRunner()

    with caplog.at_level(
        logging.INFO, logger="app.services.process_runner"
    ):
        await runner.run(
            request_for(
                sys.executable,
                "-c",
                f"print({lines!r})",
                argv_secret,
                stream_output=True,
            )
        )

    for secret in (
        "authorization-secret",
        "api-key-secret",
        "query-secret",
        "named-token-secret",
        "42.116.192.62",
        "712109809",
        argv_secret,
    ):
        assert secret not in caplog.text
    assert caplog.text.count("[REDACTED]") >= 6


@pytest.mark.asyncio
async def test_stream_output_truncates_one_oversized_record(caplog):
    runner = ProcessRunner(max_output_bytes=64)
    script = "import sys; sys.stdout.write('x' * 65 + '\\nnormal\\n')"

    with caplog.at_level(
        logging.INFO, logger="app.services.process_runner"
    ):
        await runner.run(
            request_for(
                sys.executable,
                "-c",
                script,
                stream_output=True,
            )
        )

    assert caplog.text.count("[TRUNCATED: record exceeded 64 bytes]") == 1
    assert "line='normal'" in caplog.text
    assert "x" * 65 not in caplog.text
