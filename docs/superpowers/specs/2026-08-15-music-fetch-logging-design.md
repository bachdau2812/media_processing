# Music Fetch Lifecycle Logging Design

## Goal

Make a `502 MUSIC_PROVIDER_FAILED` diagnosable from service logs by recording each music-fetch stage without exposing credentials, sensitive headers, absolute filesystem paths, or unbounded subprocess output.

## Scope

The change covers the `POST /api/v1/music/artifacts` flow from request acceptance through download, metadata extraction, artifact registration, cleanup, and error mapping. It does not add metrics, distributed tracing, retries, provider-selection changes, or live streaming of every SpotiFLAC output line.

## Logging Contract

Lifecycle events use `INFO` and include the identifiers available at that boundary:

- Request accepted: `request_id`, `track_id`.
- Capacity slot acquired: `request_id`, `track_id`.
- Job created: `request_id`, `track_id`, `job_id`.
- SpotiFLAC process started and completed: `track_id`, `job_id`, process label, exit code, elapsed milliseconds.
- Download validated: `request_id`, `track_id`, `job_id`, filename, size in bytes.
- Metadata extraction started and completed: `request_id`, `track_id`, `job_id`.
- Artifact registered: `request_id`, `track_id`, `job_id`, `artifact_id`, size in bytes, elapsed milliseconds.

Expected provider failures use `ERROR` at the API boundary with `request_id`, exception type, and the already bounded and sanitized failure detail. Process failures use `WARNING` with label, identifiers, exit code, elapsed time, and sanitized captured output. Unexpected failures continue through the existing generic exception handler.

Messages never include absolute job paths or the full command line. SpotiFLAC output remains capped at 64 KiB and passes through the existing access-token, cookie, authorization, and sensitive-header redaction before it can be logged.

## Correlation and Data Flow

The API route obtains the middleware-generated request ID and passes it explicitly to `MusicArtifactService.create`. The service owns high-level stage logs and correlates them with the generated job ID. `ProcessRequest` already carries `track_id`, `job_id`, and a safe process label, so `ProcessRunner` can log process lifecycle without receiving filesystem paths or raw arguments.

The public HTTP request and response contracts remain unchanged. `X-Request-ID` continues to identify the client-visible request.

## Error Handling

- Non-zero SpotiFLAC or ffprobe exits retain their current exception and HTTP mappings.
- Missing, multiple, empty, linked, or oversized artifacts retain their current mappings.
- A provider failure still returns the safe generic `502` response; diagnostic details appear only in server logs.
- Cleanup failures remain isolated from the original error and identify only the safe job ID.
- Cancellation and timeout behavior remain unchanged.

## Testing

Tests capture logs and verify:

1. A successful fetch emits ordered high-level stage markers and correlation identifiers.
2. Process start/completion logs contain label, job ID, exit code, and elapsed time.
3. A non-zero process exit logs bounded sanitized output.
4. Access tokens, cookies, authorization values, absolute paths, and command arguments do not appear in logs.
5. Existing API status codes, response bodies, timeout behavior, cleanup behavior, and artifact limits remain unchanged.

