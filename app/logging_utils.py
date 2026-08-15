from __future__ import annotations

import re


DEFAULT_MAX_LOG_BYTES = 64 * 1024
_SENSITIVE_NAMES = (
    r"set-cookie|cookie|authorization|proxy-authorization|"
    r"x-api-key|api-key|x-deezer-client-ip"
)
_ACCESS_TOKEN = re.compile(
    r"(?i)(access\s+token\s+acquired\s*:\s*)\S+"
)
_SENSITIVE_HEADER = re.compile(
    rf"(?i)\b({_SENSITIVE_NAMES})\s*:\s*[^\r\n]*"
)
_SENSITIVE_HEADER_TUPLE = re.compile(
    rf"(?i)\(b?'({_SENSITIVE_NAMES})',\s*b?'[^']*'\)"
)
_SENSITIVE_HEADER_TUPLE_TAIL = re.compile(
    rf"(?is)\(b?'({_SENSITIVE_NAMES})',\s*b?'.*\Z"
)
_QUERY_CREDENTIAL = re.compile(
    r"(?i)([?&](?:access_token|token|api_key|apikey|key|signature|sig|auth)=)"
    r"[^&\s'\x22<>]+"
)
_NAMED_CREDENTIAL = re.compile(
    r"(?i)\b(app_id|app_secret|api_key|access_key|client_secret|"
    r"access_token|refresh_token|id_token|auth_token|token)"
    r"\s*[=:]\s*[^,\s)'\x22]+"
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
    sanitized = _QUERY_CREDENTIAL.sub(r"\1[REDACTED]", sanitized)
    sanitized = _NAMED_CREDENTIAL.sub(r"\1=[REDACTED]", sanitized)
    return sanitized.encode("utf-8")[:maximum_bytes].decode(
        "utf-8", errors="ignore"
    )
