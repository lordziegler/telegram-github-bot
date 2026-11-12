"""Carga y valida la configuración desde variables de entorno.

Si algo falla, se detiene al arrancar y lista todos los problemas a la vez.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tgbot.domain import UserError, parse_repo

_BOT_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
_WEBHOOK_SECRET = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
LOCALES_DIR = Path(__file__).parent / "locales"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    admin_user_ids: frozenset[int]
    webhook_url: str | None
    webhook_secret: str | None
    github_token: str | None
    github_app_id: str | None
    github_app_private_key: str | None
    github_app_installation_id: int | None
    repositories: tuple[str, ...]
    timezone: ZoneInfo
    log_level: str
    database_path: str
    rate_limit_per_minute: int
    max_message_length: int
    locale: str

    @property
    def uses_github_app(self) -> bool:
        return self.github_app_id is not None

    def secrets(self) -> list[str]:
        """Todos los valores secretos, para ocultarlos en los logs."""
        values = [
            self.telegram_bot_token,
            self.github_token,
            self.webhook_secret,
            self.github_app_private_key,
        ]
        return [v for v in values if v]


def load_settings(env: Mapping[str, str]) -> Settings:
    errors: list[str] = []

    def get(name: str) -> str | None:
        value = env.get(name, "").strip()
        return value or None

    def ids(name: str) -> frozenset[int]:
        raw = get(name) or ""
        try:
            return frozenset(int(x) for x in raw.split(",") if x.strip())
        except ValueError:
            errors.append(
                f"{name}: se esperaban IDs numéricos de Telegram separados por comas; llegó {raw!r}"
            )
            return frozenset()

    def integer(name: str, default: int, lo: int, hi: int) -> int:
        raw = get(name)
        if raw is None:
            return default
        if not raw.isdigit() or not lo <= int(raw) <= hi:
            errors.append(f"{name}: se esperaba un entero entre {lo} y {hi}; llegó {raw!r}")
            return default
        return int(raw)

    token = get("TELEGRAM_BOT_TOKEN")
    if token is None:
        errors.append("Falta TELEGRAM_BOT_TOKEN: crea un bot con @BotFather y pega su token")
    elif not _BOT_TOKEN.match(token):
        errors.append("TELEGRAM_BOT_TOKEN no parece un token de BotFather (<dígitos>:<secreto>)")

    admins = ids("TELEGRAM_ADMIN_USER_IDS")
    if not admins:
        errors.append(
            "Falta TELEGRAM_ADMIN_USER_IDS: indica al menos un ID de usuario de Telegram "
            "(envía /start al bot para ver el tuyo)"
        )

    webhook_url, webhook_secret = get("TELEGRAM_WEBHOOK_URL"), get("TELEGRAM_WEBHOOK_SECRET")
    if webhook_url:
        if urlparse(webhook_url).scheme != "https":
            errors.append("TELEGRAM_WEBHOOK_URL debe ser una URL https:// (lo exige Telegram)")
        if not webhook_secret:
            errors.append(
                "TELEGRAM_WEBHOOK_SECRET es obligatorio si se define TELEGRAM_WEBHOOK_URL"
            )
    if webhook_secret and not _WEBHOOK_SECRET.match(webhook_secret):
        errors.append("TELEGRAM_WEBHOOK_SECRET: de 1 a 256 caracteres entre A-Z a-z 0-9 _ -")

    gh_token = get("GITHUB_TOKEN")
    app_id, app_key = get("GITHUB_APP_ID"), get("GITHUB_APP_PRIVATE_KEY")
    raw_installation = get("GITHUB_APP_INSTALLATION_ID")
    app_parts = [app_id, app_key, raw_installation]
    installation: int | None = None
    if any(app_parts):
        if not all(app_parts):
            errors.append(
                "La autenticación con GitHub App necesita GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY y "
                "GITHUB_APP_INSTALLATION_ID juntos"
            )
        elif raw_installation and not raw_installation.isdigit():
            errors.append("GITHUB_APP_INSTALLATION_ID debe ser numérico")
        else:
            installation = int(raw_installation) if raw_installation else None
            app_key = _load_private_key(app_key or "", errors)
    elif not gh_token:
        errors.append(
            "Faltan credenciales de GitHub: define GITHUB_TOKEN (PAT fine-grained) o el trío "
            "GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY / GITHUB_APP_INSTALLATION_ID"
        )

    repos: list[str] = []
    for item in (get("DEFAULT_REPOSITORIES") or "").split(","):
        if item.strip():
            try:
                repos.append(parse_repo(item))
            except UserError:
                errors.append(
                    f"DEFAULT_REPOSITORIES: {item.strip()!r} no tiene el formato owner/repo"
                )

    tz_name = get("DEFAULT_TIMEZONE") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(
            f"DEFAULT_TIMEZONE: zona IANA desconocida {tz_name!r} (p. ej. America/Bogota)"
        )
        tz = ZoneInfo("UTC")

    log_level = (get("LOG_LEVEL") or "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        errors.append(f"LOG_LEVEL: se esperaba DEBUG, INFO, WARNING o ERROR; llegó {log_level!r}")

    db_url = get("DATABASE_URL")
    db_path = ":memory:"
    if db_url:
        if not db_url.startswith("sqlite:///") or db_url == "sqlite:///":
            errors.append(
                "DATABASE_URL: solo se admite sqlite:///<ruta> (p. ej. sqlite:///data/bot.db)"
            )
        else:
            db_path = db_url.removeprefix("sqlite:///")

    locale = get("BOT_LOCALE") or "es"
    if not (LOCALES_DIR / f"{locale}.json").is_file():
        errors.append(f"BOT_LOCALE: no existe el archivo locales/{locale}.json")

    settings = Settings(
        telegram_bot_token=token or "",
        allowed_user_ids=ids("TELEGRAM_ALLOWED_USER_IDS"),
        allowed_chat_ids=ids("TELEGRAM_ALLOWED_CHAT_IDS"),
        admin_user_ids=admins,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        github_token=gh_token,  # si también hay App, gana la App (main.make_github)
        github_app_id=app_id,
        github_app_private_key=app_key if app_id else None,
        github_app_installation_id=installation,
        repositories=tuple(dict.fromkeys(repos)),
        timezone=tz,
        log_level=log_level,
        database_path=db_path,
        rate_limit_per_minute=integer("RATE_LIMIT_REQUESTS_PER_MINUTE", 30, 1, 1000),
        max_message_length=integer("MAX_MESSAGE_LENGTH", 4000, 500, 4096),
        locale=locale,
    )
    if errors:
        raise ConfigError("Configuración no válida:\n" + "\n".join(f"  - {e}" for e in errors))
    return settings


def _load_private_key(value: str, errors: list[str]) -> str:
    """Acepta el PEM en sí (con \\n literales) o la ruta a un archivo .pem."""
    if value.startswith("-----BEGIN"):
        return value.replace("\\n", "\n")
    path = Path(value)
    if not path.is_file():
        errors.append(
            "GITHUB_APP_PRIVATE_KEY: no es una clave PEM ni la ruta de un archivo legible"
        )
        return value
    return path.read_text()
