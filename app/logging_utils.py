from __future__ import annotations

import re


DEFAULT_MAX_LOG_BYTES = 64 * 1024
_ACCESS_TOKEN = re.compile(
    r"(?i)(access\s+token\s+acquired\s*:\s*)\S+"
)
_SENSITIVE_HEADER = re.compile(
    r"(?i)\b(set-cookie|cookie|authorization)\s*:\s*[^\r\n]*"
)
_SENSITIVE_HEADER_TUPLE = re.compile(
    r"(?i)\(b?'(set-cookie|cookie|authorization)',\s*b?'[^']*'\)"
)
_SENSITIVE_HEADER_TUPLE_TAIL = re.compile(
    r"(?is)\(b?'(set-cookie|cookie|authorization)',\s*b?'.*\Z"
)


def sanitize_log_text(
    value: bytes | str,
    maximum_bytes: int = DEFAULT_MAX_LOG_BYTES,
) -> str:
    if maximum_bytes <= 0:
        raise ValueError("Maximum log bytes must be greater than zero")
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    decoded = raw.decode("utf-8", errors="replace")
    sanitized = _ACCESS_TOKEN.sub(r"\1[REDACTED]", decoded)
    sanitized = _SENSITIVE_HEADER_TUPLE.sub(
        r"\1=[REDACTED]", sanitized
    )
    sanitized = _SENSITIVE_HEADER_TUPLE_TAIL.sub(
        r"\1=[REDACTED]", sanitized
    )
    sanitized = _SENSITIVE_HEADER.sub(r"\1=[REDACTED]", sanitized)
    return sanitized.encode("utf-8")[:maximum_bytes].decode(
        "utf-8", errors="ignore"
    )
