from __future__ import annotations

import html
import re
from urllib.parse import urlsplit, urlunsplit


REDACTED = "<СКРЫТО>"
SECRET_KEY = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|credential|oauth|encrypted[_-]?extra|"
    r"ssh|private[_-]?key|certificate|username|user[_-]?name|login|owner|editor|viewer|"
    r"sqlalchemy[_-]?uri|connection[_-]?(?:uri|string)|dsn)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|pwd|secret|client[_-]?secret|token|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|account[_-]?key|access[_-]?key|secret[_-]?key|credential|authorization|username|login)\s*[:=]"
)
BEARER_CREDENTIAL = re.compile(r"(?i)\b(?:authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9._~+\-/=]{6,}")
PEM_CREDENTIAL = re.compile(r"(?is)-----BEGIN\s+(?:[A-Z0-9 ]+\s+)?(?:PRIVATE KEY|CERTIFICATE)-----")
URL_USERINFO = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@")
TECHNICAL_NAME = re.compile(r"^(?:true|false|\d+|[0-9a-f]{16,}|[0-9a-f-]{32,})$", re.IGNORECASE)


class UnsafeExposureError(ValueError):
    pass


def remove_sensitive_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): remove_sensitive_metadata(child)
            for key, child in value.items()
            if not SECRET_KEY.search(str(key))
        }
    if isinstance(value, list):
        return [remove_sensitive_metadata(child) for child in value]
    return value


def visible_name(value: object, fallback: str) -> str:
    if isinstance(value, bool) or value is None:
        return fallback
    text = compact(value, 500)
    return fallback if not text or TECHNICAL_NAME.fullmatch(text) else text


def safe_expression(value: object) -> tuple[str, bool]:
    text = compact(value, None)
    if not text:
        return "—", False
    if _contains_secret(text):
        return REDACTED, True
    if len(text) > 8_000:
        return text[:7_940].rstrip() + " … <PARTIAL — выражение сокращено>", True
    return text, False


def safe_markdown(value: object) -> tuple[str, bool]:
    raw = str(value or "")
    unsafe = bool(re.search(r"(?is)<(?:script|style|img|iframe|object|embed)\b", raw))
    text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", raw)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = compact(text, None)
    if len(text) > 4_000:
        text = text[:3_940].rstrip() + " … <PARTIAL — текст сокращён>"
        unsafe = True
    return text or "—", unsafe


def safe_url(value: object) -> tuple[str | None, bool]:
    text = compact(value, 4_000)
    if not text:
        return None, False
    parsed = urlsplit(text)
    if parsed.scheme and parsed.scheme.casefold() not in {"http", "https"}:
        return None, True
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    if parsed.scheme:
        return urlunsplit((parsed.scheme, host, parsed.path, "", "")), bool(
            parsed.username or parsed.password or parsed.query or parsed.fragment
        )
    return parsed.path, bool(parsed.query or parsed.fragment)


def validate_safe_texts(values: list[str]) -> None:
    for value in values:
        if _contains_secret(value):
            raise UnsafeExposureError("В документе обнаружено потенциально чувствительное значение")


def _contains_secret(value: str) -> bool:
    return bool(
        SECRET_ASSIGNMENT.search(value)
        or BEARER_CREDENTIAL.search(value)
        or PEM_CREDENTIAL.search(value)
        or URL_USERINFO.search(value)
    )


def compact(value: object, limit: int | None = 4_000) -> str:
    text = " ".join(str(value or "").replace("\t", " ").split())
    if limit is not None and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text
