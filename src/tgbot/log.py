"""Logs estructurados en JSON, con ID de correlación y ocultación de secretos en el único
punto de salida."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

# Tokens que pueden aparecer sin estar en la configuración (p. ej. los que crea la App).
_TOKEN_PATTERNS = re.compile(
    r"bot\d+:[A-Za-z0-9_-]{20,}"  # token de Telegram dentro de URLs de la Bot API
    r"|\bgh[pousr]_[A-Za-z0-9_.-]{10,}"  # tokens de GitHub, incluido ghs_APPID_JWT
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = sorted((s for s in secrets if len(s) >= 6), key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return _TOKEN_PATTERNS.sub("[REDACTED]", text)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "cid": correlation_id.get(),
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return self.redact(json.dumps(payload, default=str, ensure_ascii=False))


def setup_logging(level: str, secrets: Iterable[str]) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(secrets))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # httpx registra cada URL en INFO, y las URLs de la Bot API contienen el token del bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
