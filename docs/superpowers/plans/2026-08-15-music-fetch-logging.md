# Music Fetch Lifecycle Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit safe, correlated lifecycle logs that reveal which stage caused `502 MUSIC_PROVIDER_FAILED` without changing the HTTP contract or exposing credentials.

**Architecture:** A small logging utility owns bounded redaction so subprocess and service errors share one safety boundary. `ProcessRunner` logs native process lifecycle, `MusicArtifactService` logs orchestration stages, and the API passes its request ID into the service and records the final expected failure. The Python entrypoint enables INFO for `app.*` loggers so these events appear during `python -m app` and container execution.

**Tech Stack:** Python 3.12, FastAPI, asyncio subprocesses, standard-library logging, pytest, pytest `caplog`.

## Global Constraints

- Keep all existing HTTP methods, paths, response bodies, status codes, timeouts, artifact limits, cleanup, and provider policy unchanged.
- INFO logs contain only safe identifiers, stage names, filenames rendered with `%r`, byte counts, exit codes, and elapsed milliseconds.
- Never log absolute paths or subprocess argv.
- Redact access tokens, cookie, set-cookie, authorization headers, and sensitive header tuples before output reaches a log.
- Cap captured SpotiFLAC and ffprobe output at 64 KiB.
- Keep the pre-existing staged deletion of `TELE-9W6Y7ZJ7T7U48YT7.csv` outside all implementation commits.

## File Structure

- Create `app/logging_utils.py`: one bounded redaction function shared by subprocess and orchestration logging.
- Modify `app/services/process_runner.py`: process start, completion, timeout, cancellation, and non-zero-exit logs.
- Modify `app/services/music_artifact_service.py`: correlated orchestration stage logs.
- Modify `app/api/music_artifacts.py`: pass middleware request ID into orchestration.
- Modify `app/main.py`: log expected provider failures with the client-visible request ID.
- Modify `app/__main__.py`: configure the `app` logger at INFO for real execution.
- Modify focused tests under `tests/`: prove lifecycle coverage, request correlation, visibility, and redaction.

---

### Task 1: Safe Process Lifecycle Logging

**Files:**
- Create: `app/logging_utils.py`
- Modify: `app/services/process_runner.py`
- Test: `tests/test_process_runner.py`

**Interfaces:**
- Produces: `sanitize_log_text(value: bytes | str, maximum_bytes: int = 64 * 1024) -> str`.
- Produces log events: `music_process_started`, `music_process_completed`, `music_process_failed`, `music_process_timed_out`, and `music_process_cancelled`.
- Consumes existing `ProcessRequest.label`, `track_id`, and `job_id`; it never consumes or logs `ProcessRequest.argv`.

- [ ] **Step 1: Write failing process lifecycle and redaction tests**

Add tests that capture `app.services.process_runner` logs:

```python
import logging


@pytest.mark.asyncio
async def test_process_runner_logs_safe_lifecycle_without_argv(caplog):
    runner = ProcessRunner()
    secret_argument = "argument-must-not-be-logged"

    with caplog.at_level(logging.INFO, logger="app.services.process_runner"):
        result = await runner.run(
            request_for(sys.executable, "-c", "print('ok')", secret_argument)
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

    with caplog.at_level(logging.INFO, logger="app.services.process_runner"):
        result = await runner.run(
            request_for(
                sys.executable,
                "-c",
                f"import sys; print({payload!r}); sys.exit(7)",
            )
        )

    assert result.exit_code == 7
    assert "music_process_failed" in caplog.text
    assert "authorization=[REDACTED]" in caplog.text.lower()
    assert "failure-secret" not in caplog.text
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_process_runner.py -q
```

Expected: the new tests fail because no lifecycle events are emitted.

- [ ] **Step 3: Add the shared bounded redactor**

Create `app/logging_utils.py` with the existing four redaction expressions moved from `process_runner.py` and this public boundary:

```python
DEFAULT_MAX_LOG_BYTES = 64 * 1024


def sanitize_log_text(
    value: bytes | str,
    maximum_bytes: int = DEFAULT_MAX_LOG_BYTES,
) -> str:
    if maximum_bytes <= 0:
        raise ValueError("Maximum log bytes must be greater than zero")
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    decoded = raw.decode("utf-8", errors="replace")
    sanitized = _ACCESS_TOKEN.sub(r"\1[REDACTED]", decoded)
    sanitized = _SENSITIVE_HEADER_TUPLE.sub(r"\1=[REDACTED]", sanitized)
    sanitized = _SENSITIVE_HEADER_TUPLE_TAIL.sub(r"\1=[REDACTED]", sanitized)
    sanitized = _SENSITIVE_HEADER.sub(r"\1=[REDACTED]", sanitized)
    return sanitized.encode("utf-8")[:maximum_bytes].decode(
        "utf-8", errors="ignore"
    )
```

- [ ] **Step 4: Emit process lifecycle events**

In `ProcessRunner.run`, record `started_at = time.monotonic()` after validation. Log safe fields with parameterized logging:

```python
logger.info(
    "music_process_started label=%s track_id=%s job_id=%s timeout_seconds=%s",
    request.label,
    request.track_id,
    request.job_id,
    request.timeout_seconds,
)
```

After output capture, sanitize once and log either completion or failure. Failure output must use `%r` so embedded newlines cannot forge log records:

```python
if process.returncode == 0:
    logger.info(
        "music_process_completed label=%s track_id=%s job_id=%s "
        "exit_code=%s elapsed_ms=%s",
        request.label,
        request.track_id,
        request.job_id,
        process.returncode,
        elapsed_ms,
    )
else:
    logger.warning(
        "music_process_failed label=%s track_id=%s job_id=%s "
        "exit_code=%s elapsed_ms=%s output=%r",
        request.label,
        request.track_id,
        request.job_id,
        process.returncode,
        elapsed_ms,
        sanitized_output,
    )
```

Timeout and cancellation log their event name, label, identifiers, and elapsed time after terminating the process. They do not log argv or paths:

```python
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
        f"{request.label} timed out after {request.timeout_seconds} seconds"
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
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_process_runner.py -q
```

Expected: all process-runner tests pass and the redaction regression remains green.

- [ ] **Step 6: Commit Task 1 only**

```powershell
git add app/logging_utils.py app/services/process_runner.py tests/test_process_runner.py
git commit --only app/logging_utils.py app/services/process_runner.py tests/test_process_runner.py -m "feat: log safe music process lifecycle"
```

---

### Task 2: Correlated Music Orchestration Logs

**Files:**
- Modify: `app/services/music_artifact_service.py`
- Modify: `app/api/music_artifacts.py`
- Modify: `app/main.py`
- Modify: `tests/test_music_artifact_service.py`
- Modify: `tests/test_music_artifact_api.py`

**Interfaces:**
- Changes internal method to `MusicArtifactService.create(track_id: str, request_id: str) -> MusicArtifactResponse`.
- The HTTP request and response contract remains unchanged.
- Produces log events: `music_fetch_started`, `music_capacity_acquired`, `music_job_created`, `music_download_started`, `music_download_completed`, `music_metadata_started`, `music_metadata_completed`, `music_artifact_registered`, `music_fetch_failed`, and `music_provider_failed`.

- [ ] **Step 1: Write failing orchestration and request-correlation tests**

Update the API fake service to accept and record `request_id`:

```python
class FakeMusicService:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[tuple[str, str]] = []

    async def create(self, track_id: str, request_id: str):
        self.requests.append((track_id, request_id))
        if self.error:
            raise self.error
        return self.response
```

Add a successful service test using `caplog`:

```python
REQUEST_ID = "10000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_create_logs_correlated_music_fetch_stages(tmp_path, caplog):
    events: list[str] = []
    service = MusicArtifactService(
        settings(tmp_path),
        FakeDownloader(events),
        FakeMetadataReader(events),
        FakeArtifactStore(events),
    )

    with caplog.at_level(
        logging.INFO,
        logger="app.services.music_artifact_service",
    ):
        response = await service.create(TRACK_ID, REQUEST_ID)

    for event in (
        "music_fetch_started",
        "music_capacity_acquired",
        "music_job_created",
        "music_download_started",
        "music_download_completed",
        "music_metadata_started",
        "music_metadata_completed",
        "music_artifact_registered",
    ):
        assert event in caplog.text
    assert f"request_id={REQUEST_ID}" in caplog.text
    assert f"track_id={TRACK_ID}" in caplog.text
    assert f"artifact_id={response.artifact_id}" in caplog.text
    assert str(tmp_path.resolve()) not in caplog.text
```

Add an API test asserting the service receives the same UUID returned in `X-Request-ID`:

```python
@pytest.mark.asyncio
async def test_post_passes_response_request_id_to_music_service(tmp_path):
    music_service = FakeMusicService(response_model())
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(music_service, FakeStore(None)),
    )

    response = await request(
        app,
        "POST",
        "/api/v1/music/artifacts",
        json={"trackId": TRACK_ID},
    )

    assert music_service.requests == [
        (TRACK_ID, response.headers["x-request-id"])
    ]
```

Add a provider-failure test asserting safe error logging while the HTTP body remains generic:

```python
@pytest.mark.asyncio
async def test_provider_failure_logs_sanitized_detail_and_request_id(
    tmp_path,
    caplog,
):
    error = MusicProviderFailed(
        "Authorization: Bearer provider-secret"
    )
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(FakeMusicService(error=error), FakeStore(None)),
    )

    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = await request(
            app,
            "POST",
            "/api/v1/music/artifacts",
            json={"trackId": TRACK_ID},
        )

    assert_problem(response, 502, "MUSIC_PROVIDER_FAILED")
    assert "music_provider_failed" in caplog.text
    assert response.headers["x-request-id"] in caplog.text
    assert "authorization=[REDACTED]" in caplog.text.lower()
    assert "provider-secret" not in caplog.text
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_music_artifact_service.py tests/test_music_artifact_api.py -q
```

Expected: new assertions fail because request ID is not passed and lifecycle logs do not exist.

- [ ] **Step 3: Pass request ID into orchestration**

Change the route call to:

```python
return await http_request.app.state.services.music_artifact_service.create(
    request.track_id,
    http_request.state.request_id,
)
```

Update `MusicArtifactService.create` to require the second `request_id` argument. Replace each existing direct `service.create(TRACK_ID)` test call with `service.create(TRACK_ID, REQUEST_ID)` and both capacity-test calls with the same two arguments.

- [ ] **Step 4: Add safe stage logs and elapsed timing**

Use `time.monotonic`, safe UUID/alphanumeric identifiers, `%r` for filenames, and a `stage` variable. Emit INFO before and after each external boundary. On failure emit:

```python
logger.warning(
    "music_fetch_failed request_id=%s track_id=%s job_id=%s "
    "stage=%s error_type=%s elapsed_ms=%s detail=%r",
    request_id,
    track_id,
    job_id or "unassigned",
    stage,
    type(error).__name__,
    elapsed_ms,
    sanitize_log_text(str(error)),
)
```

The provider exception handler logs `music_provider_failed` at ERROR with request ID, exception type, and `sanitize_log_text(str(error))`, then returns the existing safe 502 body unchanged.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_music_artifact_service.py tests/test_music_artifact_api.py -q
```

Expected: all focused orchestration and API tests pass.

- [ ] **Step 6: Commit Task 2 only**

```powershell
git add app/services/music_artifact_service.py app/api/music_artifacts.py app/main.py tests/test_music_artifact_service.py tests/test_music_artifact_api.py
git commit --only app/services/music_artifact_service.py app/api/music_artifacts.py app/main.py tests/test_music_artifact_service.py tests/test_music_artifact_api.py -m "feat: log correlated music fetch stages"
```

---

### Task 3: Enable INFO Logs in the Runtime Entrypoint

**Files:**
- Modify: `app/__main__.py`
- Create: `tests/test_entrypoint_logging.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `configure_logging() -> None` in `app.__main__`.
- Sets logger `app` to INFO and configures a timestamped standard stream handler only when the process has no root handlers.

- [ ] **Step 1: Write a failing entrypoint logging test**

```python
import logging

from app.__main__ import configure_logging


def test_configure_logging_enables_app_info_messages():
    app_logger = logging.getLogger("app")
    previous_level = app_logger.level
    try:
        app_logger.setLevel(logging.WARNING)
        configure_logging()
        assert app_logger.isEnabledFor(logging.INFO)
    finally:
        app_logger.setLevel(previous_level)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m pytest tests/test_entrypoint_logging.py -q
```

Expected: collection fails because `configure_logging` does not exist.

- [ ] **Step 3: Add minimal runtime logging configuration**

Add:

```python
def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("app").setLevel(logging.INFO)
```

Call `configure_logging()` at the start of `main()` before `uvicorn.run`. Document the stage names and the rule that failure output is sanitized in README.

- [ ] **Step 4: Run focused and full verification**

Run:

```powershell
python -m pytest tests/test_entrypoint_logging.py tests/test_process_runner.py tests/test_music_artifact_service.py tests/test_music_artifact_api.py -q
python -m pytest -q
python -m py_compile app/__main__.py app/logging_utils.py app/main.py app/api/music_artifacts.py app/services/process_runner.py app/services/music_artifact_service.py
git diff --check
```

Expected: all tests pass, compile exits 0, and diff check reports no errors. Windows symlink tests may retain their existing privilege-based skips.

- [ ] **Step 5: Commit Task 3 only**

```powershell
git add app/__main__.py tests/test_entrypoint_logging.py README.md
git commit --only app/__main__.py tests/test_entrypoint_logging.py README.md -m "docs: expose music fetch lifecycle logs"
```

- [ ] **Step 6: Confirm repository state**

Run:

```powershell
git show --check --stat --oneline HEAD
git status --short
```

Expected: implementation files are committed and the pre-existing staged CSV deletion is the only unrelated status entry.
