# Live SpotiFLAC Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream every bounded, sanitized SpotiFLAC output record to INFO logs in real time while keeping ffprobe output private and preserving existing capture and process behavior.

**Architecture:** `ProcessRequest` explicitly opts into streaming. `ProcessRunner` feeds each stdout/stderr chunk to both the existing bounded capture and a bounded CR/LF record framer; framed records pass through the shared redactor and are logged with process correlation fields. `SpotiFlacDownloader` enables the flag, while ffprobe and all other process requests retain the safe default.

**Tech Stack:** Python 3.12, asyncio subprocesses, standard-library logging, pytest, pytest `caplog`.

## Global Constraints

- Live output is enabled by default for every SpotiFLAC fetch.
- ffprobe stdout is never streamed.
- Both `\n` and `\r` delimiters emit records; empty records are ignored.
- A logical record larger than 64 KiB emits one truncation marker and discards raw bytes until the next delimiter.
- Every emitted line is sanitized before logging and rendered with `%r`.
- Never log subprocess argv or absolute paths.
- Existing 64 KiB `ProcessResult.output`, timeout, cancellation, process-group cleanup, HTTP contracts, provider options, artifact limits, and cleanup remain unchanged.
- Keep the pre-existing staged deletion of `TELE-9W6Y7ZJ7T7U48YT7.csv` outside every commit.

---

### Task 1: Stream Bounded Sanitized Process Records

**Files:**
- Modify: `app/logging_utils.py`
- Modify: `app/services/process_runner.py`
- Modify: `app/services/spotiflac_downloader.py`
- Modify: `tests/test_process_runner.py`
- Modify: `tests/test_spotiflac_downloader.py`
- Modify: `tests/test_ffprobe_metadata.py`
- Modify: `README.md`

**Interfaces:**
- `ProcessRequest(..., stream_output: bool = False)` opts into live records.
- `_StreamRecordFramer.feed(chunk: bytes) -> list[bytes]` emits CR/LF-delimited records or the truncation marker.
- `_StreamRecordFramer.finish() -> list[bytes]` emits a final bounded partial record.
- SpotiFLAC requests set `stream_output=True`; ffprobe requests keep `False`.

- [ ] **Step 1: Write failing live-stream, privacy, and framing tests**

Extend the test helper so tests can opt in:

```python
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
```

Add a real subprocess test proving the first line is logged before process completion and CR/LF ordering is retained:

```python
@pytest.mark.asyncio
async def test_stream_enabled_logs_cr_lf_records_before_process_finishes(
    caplog,
):
    runner = ProcessRunner()
    script = (
        "import sys,time; "
        "sys.stdout.write('first\\n'); sys.stdout.flush(); "
        "time.sleep(0.5); "
        "sys.stdout.write('second\\rthird\\nlast'); sys.stdout.flush()"
    )

    with caplog.at_level(logging.INFO, logger="app.services.process_runner"):
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
        for _ in range(40):
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
    assert result.output == "first\nsecond\rthird\nlast"
```

Add a default-off test:

```python
@pytest.mark.asyncio
async def test_stream_disabled_does_not_log_process_output(caplog):
    runner = ProcessRunner()
    with caplog.at_level(logging.INFO, logger="app.services.process_runner"):
        result = await runner.run(
            request_for(sys.executable, "-c", "print('captured-only')")
        )
    assert result.output.strip() == "captured-only"
    assert "music_process_output" not in caplog.text
```

Add a live redaction test whose single records contain authorization, API key tuple, signed query token, Deezer client IP tuple, and Qobuz app ID. Assert each secret and a secret argv value are absent while `[REDACTED]` appears.

```python
@pytest.mark.asyncio
async def test_stream_output_redacts_credentials_and_never_logs_argv(caplog):
    lines = (
        "Authorization: Bearer authorization-secret\n"
        "(b'x-api-key', b'api-key-secret')\n"
        "https://provider.invalid/file?token=query-secret&format=flac\n"
        "(b'x-deezer-client-ip', b'42.116.192.62')\n"
        "fresh credentials (app_id=712109809)\n"
    )
    argv_secret = "argv-secret"
    runner = ProcessRunner()
    with caplog.at_level(logging.INFO, logger="app.services.process_runner"):
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
        "42.116.192.62",
        "712109809",
        argv_secret,
    ):
        assert secret not in caplog.text
    assert caplog.text.count("[REDACTED]") >= 5
```

Add an oversized-record test with `ProcessRunner(max_output_bytes=64)` and a child that writes 65 `x` bytes followed by `\nnormal\n`. Assert exactly one `music_process_output ... [TRUNCATED: record exceeded 64 bytes]` event, a later `line='normal'`, and no 65-byte raw record.

Update the exact SpotiFLAC request assertion with:

```python
assert captured_request.stream_output is True
```

Update the ffprobe request assertion with:

```python
assert request.stream_output is False
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_process_runner.py tests/test_spotiflac_downloader.py tests/test_ffprobe_metadata.py -q
```

Expected: failures show `ProcessRequest` has no `stream_output` field and no `music_process_output` records exist.

- [ ] **Step 3: Expand the shared redaction boundary**

In `app/logging_utils.py`, define sensitive names once and use them in header and tuple patterns:

```python
_SENSITIVE_NAMES = (
    r"set-cookie|cookie|authorization|proxy-authorization|"
    r"x-api-key|api-key|x-deezer-client-ip"
)
_SENSITIVE_HEADER = re.compile(
    rf"(?i)\b({_SENSITIVE_NAMES})\s*:\s*[^\r\n]*"
)
_SENSITIVE_HEADER_TUPLE = re.compile(
    rf"(?i)\(b?'({_SENSITIVE_NAMES})',\s*b?'[^']*'\)"
)
_QUERY_CREDENTIAL = re.compile(
    r"(?i)([?&](?:access_token|token|api_key|apikey|key|signature|sig|auth)=)"
    r"[^&\s'\"<>]+"
)
_NAMED_CREDENTIAL = re.compile(
    r"(?i)\b(app_id|app_secret|api_key|access_key|client_secret)\s*[=:]\s*"
    r"[^,\s)'\"]+"
)
```

Apply tuple and header redaction before query and named credential substitutions. Preserve the existing access-token and unterminated-sensitive-tuple behavior.

- [ ] **Step 4: Implement the bounded record framer and live event**

Add to `ProcessRequest`:

```python
stream_output: bool = False
```

Add a framer that holds at most `maximum_bytes + 1`, emits non-empty records on byte `10` or `13`, emits one marker when a record exceeds the limit, and discards until the next delimiter:

```python
class _StreamRecordFramer:
    def __init__(self, maximum_bytes: int) -> None:
        self._maximum_bytes = maximum_bytes
        self._pending = bytearray()
        self._discarding = False

    def feed(self, chunk: bytes) -> list[bytes]:
        records: list[bytes] = []
        for value in chunk:
            if value in (10, 13):
                if not self._discarding and self._pending:
                    records.append(bytes(self._pending))
                self._pending.clear()
                self._discarding = False
                continue
            if self._discarding:
                continue
            self._pending.append(value)
            if len(self._pending) > self._maximum_bytes:
                records.append(
                    f"[TRUNCATED: record exceeded {self._maximum_bytes} bytes]"
                    .encode("ascii")
                )
                self._pending.clear()
                self._discarding = True
        return records

    def finish(self) -> list[bytes]:
        if self._discarding or not self._pending:
            return []
        record = bytes(self._pending)
        self._pending.clear()
        return [record]
```

Change `_read_output` to accept `request`, preserve current bounded capture, feed chunks only when `request.stream_output`, and emit final partial records in `finally`. Log each record through a helper:

```python
def _log_process_output(self, request: ProcessRequest, record: bytes) -> None:
    try:
        safe_line = sanitize_log_text(record, self._max_output_bytes)
        logger.info(
            "music_process_output label=%s track_id=%s job_id=%s line=%r",
            request.label,
            request.track_id,
            request.job_id,
            safe_line,
        )
    except Exception:
        logger.warning(
            "music_process_output_logging_failed label=%s track_id=%s "
            "job_id=%s",
            request.label,
            request.track_id,
            request.job_id,
        )
```

- [ ] **Step 5: Enable streaming only for SpotiFLAC and document it**

Set `stream_output=True` in the `ProcessRequest` created by `SpotiFlacDownloader`. Do not set it in `FfprobeMetadataReader`. Update README to state that all bounded sanitized SpotiFLAC output records are emitted at INFO by default and ffprobe JSON is excluded.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_process_runner.py tests/test_spotiflac_downloader.py tests/test_ffprobe_metadata.py -q
```

Expected: all focused tests pass; Windows symlink tests may retain existing privilege-based skips.

- [ ] **Step 7: Run full verification**

Run:

```powershell
python -m pytest -q
python -m py_compile app/logging_utils.py app/services/process_runner.py app/services/spotiflac_downloader.py app/services/ffprobe_metadata.py
git diff --check
```

Expected: full tests pass, compile exits 0, and diff check is clean.

- [ ] **Step 8: Commit implementation only**

```powershell
git add app/logging_utils.py app/services/process_runner.py app/services/spotiflac_downloader.py tests/test_process_runner.py tests/test_spotiflac_downloader.py tests/test_ffprobe_metadata.py README.md
git commit --only app/logging_utils.py app/services/process_runner.py app/services/spotiflac_downloader.py tests/test_process_runner.py tests/test_spotiflac_downloader.py tests/test_ffprobe_metadata.py README.md -m "feat: stream sanitized SpotiFLAC output"
```

