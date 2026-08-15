# Live SpotiFLAC Output Design

## Goal

Show the complete SpotiFLAC download process in the service terminal as it happens, enabled by default for every music fetch, while preserving request correlation and preventing credential, personal-data, or log-injection leaks.

## Scope

Live output applies only to subprocess requests explicitly marked for streaming. `SpotiFlacDownloader` enables it for the `SpotiFLAC` request. `ffprobe` remains non-streaming because its stdout is machine-readable JSON that can contain metadata and lyrics. HTTP endpoints, response models, provider order, quality, retry count, timeout, artifact validation, cleanup, and size limits remain unchanged.

## Output Contract

Each completed logical output record is emitted immediately at INFO:

```text
music_process_output label=SpotiFLAC track_id=<track> job_id=<uuid> line='<sanitized text>'
```

Both line-feed and carriage-return delimiters terminate records, so normal log lines and progress-bar updates are visible. Empty records are ignored. The original subprocess severity is kept inside the sanitized `line` text while the service event itself uses INFO, ensuring verbose SpotiFLAC output is visible under the existing runtime logging configuration.

`ProcessRequest` gains `stream_output: bool = False`. `SpotiFlacDownloader` sets it to `True`; all existing callers, including `ffprobe`, retain `False` unless explicitly enabled.

## Streaming and Capture

`ProcessRunner` continues to merge stderr into stdout and capture at most 64 KiB for `ProcessResult`. Streaming is an additional observation path and does not change the bytes returned to callers.

The reader incrementally frames records on `\n` or `\r`. A partial record is held only until the next delimiter. A record exceeding 64 KiB is not logged verbatim; it emits one safe truncation marker and discards bytes until its delimiter. This bounds memory and prevents a child process from generating an unbounded log record.

## Security

Every logical record passes through the shared redaction boundary before logging. Redaction covers:

- access tokens;
- cookie and set-cookie values;
- authorization and proxy-authorization values;
- sensitive header tuples;
- API-key headers and common key/token/signature query parameters;
- Deezer client IP headers;
- Qobuz credential values printed as `app_id` or credential fields.

Messages use parameterized logging and `%r` for the sanitized line, so newlines, control characters, and terminal escape sequences cannot forge independent log records. The subprocess argv and absolute paths are never included.

## Error Handling

Live logging failures must not interrupt the download or alter its exit status. Timeout, cancellation, process-group termination, output capture, final validation, and existing safe failure summaries remain unchanged. If a final partial record exists when the process closes, it is sanitized and emitted once.

## Testing

Tests prove that:

1. stream-enabled output appears incrementally and in order for both `\n` and `\r` delimiters;
2. stream-disabled requests return captured output without `music_process_output` events;
3. secrets, API keys, signed query credentials, client IPs, argv, and absolute paths do not enter live logs;
4. oversized records emit one truncation marker and remain memory bounded;
5. `SpotiFlacDownloader` enables streaming while `ffprobe` does not;
6. timeout, cancellation, output cap, redaction, API, and full service regressions remain green.

