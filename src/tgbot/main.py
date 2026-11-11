"""Punto de entrada: `python -m tgbot` arranca el bot; `python -m tgbot healthcheck`
comprueba que sigue vivo."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from telegram import Update
from telegram.error import InvalidToken
from telegram.ext import Application

from tgbot.bot import Runtime, build_application, publish_commands
from tgbot.config import ConfigError, Settings, load_settings
from tgbot.github import GitHub
from tgbot.log import setup_logging
from tgbot.store import Store

log = logging.getLogger("tgbot")
HEARTBEAT = Path(tempfile.gettempdir()) / "tgbot.heartbeat"
HEARTBEAT_EVERY = 30
WEBHOOK_PORT = 8080  # dentro del contenedor; publícalo o pon un proxy
UPDATES = [
    Update.MESSAGE,
    Update.CALLBACK_QUERY,
]  # sin mensajes editados: nada se ejecuta dos veces


def make_github(settings: Settings) -> GitHub:
    if settings.uses_github_app:
        try:
            import jwt  # noqa: F401
        except ImportError:
            raise ConfigError(
                "GitHub App necesita el extra: pip install 'telegram-github-bot[github-app]'"
            ) from None
        return GitHub(
            app_id=settings.github_app_id,
            app_private_key=settings.github_app_private_key,
            installation_id=settings.github_app_installation_id,
        )
    return GitHub(settings.github_token)


async def _heartbeat(rt: Runtime) -> None:
    while True:
        HEARTBEAT.touch()
        rt.limiter.prune()
        await asyncio.sleep(HEARTBEAT_EVERY)


def build(settings: Settings) -> Application:  # type: ignore[type-arg]
    store = Store(settings)
    gh = make_github(settings)
    app = build_application(settings, store, gh)
    tasks: list[asyncio.Task[None]] = []

    async def post_init(app: Application) -> None:  # type: ignore[type-arg]
        await publish_commands(app)
        tasks.append(asyncio.create_task(_heartbeat(app.bot_data["runtime"])))
        me = app.bot.username
        log.info("bot arrancado", extra={"bot": me, "repos": len(store.repos())})

    async def post_shutdown(_: Application) -> None:  # type: ignore[type-arg]
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await gh.aclose()
        store.db.close()
        HEARTBEAT.unlink(missing_ok=True)
        log.info("bot detenido")

    app.post_init = post_init
    app.post_shutdown = post_shutdown
    return app


def healthcheck() -> int:
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except FileNotFoundError:
        return 1
    return 0 if age < HEARTBEAT_EVERY * 3 else 1


def main() -> None:
    if sys.argv[1:] == ["healthcheck"]:
        sys.exit(healthcheck())
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        print("Consulta .env.example y docs/configuration.md", file=sys.stderr)
        sys.exit(2)
    setup_logging(settings.log_level, settings.secrets())
    if settings.database_path == ":memory:":
        log.warning(
            "DATABASE_URL vacío: los roles y repos añadidos desde Telegram se pierden al reiniciar"
        )
    # run_polling / run_webhook se detienen limpiamente con SIGINT, SIGTERM y SIGABRT.
    # Cualquier error que escape pasa por el logger que oculta secretos: el mensaje de
    # InvalidToken de PTB contiene el propio token, y un traceback en stderr lo filtraría.
    try:
        app = build(settings)
        if settings.webhook_url:
            app.run_webhook(
                listen="0.0.0.0",  # noqa: S104 - puerto del contenedor; TLS lo pone el proxy
                port=WEBHOOK_PORT,
                url_path=urlparse(settings.webhook_url).path.lstrip("/"),
                webhook_url=settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=UPDATES,
            )
        else:
            app.run_polling(allowed_updates=UPDATES)
    except ConfigError as exc:
        log.error(str(exc))
        sys.exit(2)
    except InvalidToken:
        log.error("Telegram rechazó TELEGRAM_BOT_TOKEN: revísalo con @BotFather")
        sys.exit(1)
    except Exception:
        log.exception("el bot se cayó")
        sys.exit(1)
