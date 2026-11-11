"""Load and validate configuration from environment variables. Fails fast with every problem."""

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
        """Every secret value, for log redaction."""
        values = [self.telegram_bot_token, self.github_token, self.webhook_secret]
        values.append(self.github_app_private_key)
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
            errors.append(f"{name}: expected comma-separated numeric Telegram IDs, got {raw!r}")
            return frozenset()

    def integer(name: str, default: int, lo: int, hi: int) -> int:
        raw = get(name)
        if raw is None:
            return default
        if not raw.isdigit() or not lo <= int(raw) <= hi:
            errors.append(f"{name}: expected an integer between {lo} and {hi}, got {raw!r}")
            return default
        return int(raw)

    token = get("TELEGRAM_BOT_TOKEN")
    if token is None:
        errors.append(
            "TELEGRAM_BOT_TOKEN is missing: create a bot with @BotFather and paste its token"
        )
    elif not _BOT_TOKEN.match(token):
        errors.append("TELEGRAM_BOT_TOKEN does not look like a BotFather token (<digits>:<secret>)")

    admins = ids("TELEGRAM_ADMIN_USER_IDS")
    if not admins:
        errors.append(
            "TELEGRAM_ADMIN_USER_IDS is missing: set at least one Telegram user ID "
            "(send /start to the bot to see yours)"
        )

    webhook_url, webhook_secret = get("TELEGRAM_WEBHOOK_URL"), get("TELEGRAM_WEBHOOK_SECRET")
    if webhook_url:
        if urlparse(webhook_url).scheme != "https":
            errors.append("TELEGRAM_WEBHOOK_URL must be an https:// URL (Telegram requirement)")
        if not webhook_secret:
            errors.append("TELEGRAM_WEBHOOK_SECRET is required when TELEGRAM_WEBHOOK_URL is set")
    if webhook_secret and not _WEBHOOK_SECRET.match(webhook_secret):
        errors.append("TELEGRAM_WEBHOOK_SECRET: 1-256 characters from A-Z a-z 0-9 _ -")

    gh_token = get("GITHUB_TOKEN")
    app_id, app_key = get("GITHUB_APP_ID"), get("GITHUB_APP_PRIVATE_KEY")
    raw_installation = get("GITHUB_APP_INSTALLATION_ID")
    app_parts = [app_id, app_key, raw_installation]
    installation: int | None = None
    if any(app_parts):
        if not all(app_parts):
            errors.append(
                "GitHub App auth needs GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY and "
                "GITHUB_APP_INSTALLATION_ID together"
            )
        elif raw_installation and not raw_installation.isdigit():
            errors.append("GITHUB_APP_INSTALLATION_ID must be numeric")
        else:
            installation = int(raw_installation) if raw_installation else None
            app_key = _load_private_key(app_key or "", errors)
    elif not gh_token:
        errors.append(
            "GitHub credentials missing: set GITHUB_TOKEN (fine-grained PAT) or the "
            "GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY / GITHUB_APP_INSTALLATION_ID trio"
        )

    repos: list[str] = []
    for item in (get("DEFAULT_REPOSITORIES") or "").split(","):
        if item.strip():
            try:
                repos.append(parse_repo(item))
            except UserError:
                errors.append(f"DEFAULT_REPOSITORIES: {item.strip()!r} is not owner/repo")

    tz_name = get("DEFAULT_TIMEZONE") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"DEFAULT_TIMEZONE: unknown IANA zone {tz_name!r} (e.g. America/Bogota)")
        tz = ZoneInfo("UTC")

    log_level = (get("LOG_LEVEL") or "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        errors.append(f"LOG_LEVEL: expected DEBUG, INFO, WARNING or ERROR, got {log_level!r}")

    db_url = get("DATABASE_URL")
    db_path = ":memory:"
    if db_url:
        if not db_url.startswith("sqlite:///"):
            errors.append(
                "DATABASE_URL: only sqlite:///<path> is supported (e.g. sqlite:///data/bot.db)"
            )
        else:
            db_path = db_url.removeprefix("sqlite:///")

    locale = get("BOT_LOCALE") or "es"
    if not (LOCALES_DIR / f"{locale}.json").is_file():
        errors.append(f"BOT_LOCALE: no file locales/{locale}.json")

    settings = Settings(
        telegram_bot_token=token or "",
        allowed_user_ids=ids("TELEGRAM_ALLOWED_USER_IDS"),
        allowed_chat_ids=ids("TELEGRAM_ALLOWED_CHAT_IDS"),
        admin_user_ids=admins,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        github_token=None if app_id else gh_token,  # the App wins when both are set
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
        raise ConfigError("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))
    return settings


def _load_private_key(value: str, errors: list[str]) -> str:
    """Accept the PEM itself (literal \\n allowed) or a path to a .pem file."""
    if value.startswith("-----BEGIN"):
        return value.replace("\\n", "\n")
    path = Path(value)
    if not path.is_file():
        errors.append("GITHUB_APP_PRIVATE_KEY: neither a PEM key nor a readable file path")
        return value
    return path.read_text()
