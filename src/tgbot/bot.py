"""Adaptador de Telegram: registro de comandos, autorización, límite de frecuencia,
confirmaciones y paginación.

Todo comando pasa por `Runtime.dispatch`, así que uno nuevo no puede saltarse la autorización.
"""

from __future__ import annotations

import html
import importlib
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial

from telegram import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    filters,
)
from telegram.request import BaseRequest

from tgbot.config import LOCALES_DIR, Settings
from tgbot.domain import RateLimiter, Role, UserError
from tgbot.github import GitHub, GitHubError
from tgbot.log import correlation_id
from tgbot.store import Store

log = logging.getLogger(__name__)
CONFIRM_TTL = 300.0
BUTTON_TTL = 1800.0


class Html(str):
    """Parámetro de traducción que ya es HTML y no debe escaparse otra vez."""


class I18n:
    def __init__(self, locale: str) -> None:
        self.strings: dict[str, str] = json.loads((LOCALES_DIR / f"{locale}.json").read_text())

    def __call__(self, key: str, **params: object) -> str:
        """Las plantillas son HTML; los parámetros se escapan salvo que ya sean `Html` (p. ej. otro
        t()). Devuelve una instancia de `Html`, así que una traducción puede ir dentro de otra."""
        template = self.strings.get(key, key)
        safe = {k: v if isinstance(v, Html) else html.escape(str(v)) for k, v in params.items()}
        return Html(template.format(**safe))


@dataclass
class Reply:
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)  # (texto, callback_data)


Handler = Callable[["Ctx", str], Awaitable[Reply | str]]


@dataclass(frozen=True)
class Command:
    name: str
    role: Role | None  # None: cualquiera, incluso sin acceso (/start, /help, /whoami)
    handler: Handler
    per_minute: int | None  # límite opcional por comando, además del límite por usuario


COMMANDS: dict[str, Command] = {}


def command(
    name: str, role: Role | None, per_minute: int | None = None
) -> Callable[[Handler], Handler]:
    """Registra un comando. Pon el módulo en `tgbot/commands/` y se carga solo."""

    def register(handler: Handler) -> Handler:
        if name in COMMANDS:
            raise ValueError(f"duplicate command /{name}")
        COMMANDS[name] = Command(name, role, handler, per_minute)
        return handler

    return register


@dataclass
class Interaction:
    owner: int
    role: Role
    expires: float
    run: Callable[[str], Awaitable[Reply]]
    edit: bool  # editar el mensaje del botón, o responder con uno nuevo


@dataclass
class Ctx:
    """Lo que recibe un comando. `Runtime.dispatch` lo construye en cada update."""

    rt: Runtime
    command: str
    min_role: Role | None
    user_id: int
    user_name: str
    role: Role | None

    @property
    def t(self) -> I18n:
        return self.rt.t

    @property
    def gh(self) -> GitHub:
        return self.rt.gh

    @property
    def store(self) -> Store:
        return self.rt.store

    @property
    def settings(self) -> Settings:
        return self.rt.settings

    @property
    def repo(self) -> str:
        return self.store.selected(self.user_id)

    def require(self, role: Role) -> None:
        if self.role is None or self.role < role:
            raise UserError(
                "forbidden",
                command=self.command,
                required=role.label,
                role=self.role.label if self.role else "-",
                user_id=self.user_id,
            )

    def date(self, iso: str | None) -> str:
        if not iso:
            return "-"
        return (
            datetime.fromisoformat(iso)
            .astimezone(self.settings.timezone)
            .strftime("%Y-%m-%d %H:%M")
        )

    def confirm(
        self, prompt: str, action: Callable[[], Awaitable[str]], role: Role | None = None
    ) -> Reply:
        """Pregunta antes de ejecutar `action`. Solo confirma quien lo pidió, si conserva `role`."""

        async def run(arg: str) -> Reply:
            return Reply(await action()) if arg == "ok" else Reply(self.t("cancelled"))

        token = self.rt.register(
            self.user_id, role or self.min_role or Role.VIEWER, run, CONFIRM_TTL
        )
        return Reply(
            prompt + "\n\n" + self.t("confirm_question"),
            [[(self.t("btn_confirm"), f"{token}:ok"), (self.t("btn_cancel"), f"{token}:no")]],
        )

    async def paginate(self, render: Callable[[int], Awaitable[tuple[Reply | str, bool]]]) -> Reply:
        """`render(page)` devuelve (respuesta, hay_siguiente). Añade botones ◀ ▶ de este usuario."""

        async def run(arg: str) -> Reply:
            page = int(arg) if arg.isdigit() and int(arg) > 0 else 1
            result, has_next = await render(page)
            reply = Reply(result) if isinstance(result, str) else result
            nav = []
            if page > 1:
                nav.append(("◀", f"{token}:{page - 1}"))
            if has_next:
                nav.append(("▶", f"{token}:{page + 1}"))
            return Reply(reply.text, reply.buttons + ([nav] if nav else []))

        token = self.rt.register(self.user_id, self.min_role or Role.VIEWER, run, BUTTON_TTL)
        return await run("1")

    def button(self, label: str, action: Callable[[], Awaitable[Reply | str]]) -> tuple[str, str]:
        """Botón que responde con un mensaje nuevo (p. ej. el detalle de un elemento)."""

        async def run(_: str) -> Reply:
            result = await action()
            return Reply(result) if isinstance(result, str) else result

        token = self.rt.register(
            self.user_id, self.min_role or Role.VIEWER, run, BUTTON_TTL, edit=False
        )
        return (label, f"{token}:go")


class Runtime:
    def __init__(self, settings: Settings, store: Store, gh: GitHub) -> None:
        self.settings = settings
        self.store = store
        self.gh = gh
        self.t = I18n(settings.locale)
        self.limiter = RateLimiter()
        self.interactions: dict[str, Interaction] = {}

    def register(
        self,
        owner: int,
        role: Role,
        run: Callable[[str], Awaitable[Reply]],
        ttl: float,
        *,
        edit: bool = True,
    ) -> str:
        now = time.monotonic()
        for key in [k for k, v in self.interactions.items() if v.expires < now]:
            del self.interactions[key]
        token = secrets.token_urlsafe(9)
        self.interactions[token] = Interaction(owner, role, now + ttl, run, edit)
        return token

    def _chat_allowed(self, update: Update) -> bool:
        chat = update.effective_chat
        return chat is not None and (
            chat.type == "private" or chat.id in self.settings.allowed_chat_ids
        )

    def _rate_limited(self, user_id: int, cmd: str, per_minute: int | None) -> bool:
        limit = self.settings.rate_limit_per_minute
        if not self.limiter.allow(("user", user_id), limit):
            return True
        return per_minute is not None and not self.limiter.allow(("cmd", user_id, cmd), per_minute)

    async def dispatch(self, cmd: Command, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        message, user = update.effective_message, update.effective_user
        if message is None or user is None or not self._chat_allowed(update):
            return
        cid = correlation_id.set(f"u{update.update_id}")
        role = self.store.role_of(user.id)
        ctx = Ctx(self, cmd.name, cmd.role, user.id, user.full_name, role)
        extra = {"user_id": user.id, "chat_id": message.chat_id, "command": cmd.name}
        try:
            if self._rate_limited(user.id, cmd.name, cmd.per_minute):
                log.info("límite de frecuencia", extra=extra)
                await self._send(message, Reply(self.t("rate_limited")))
                return
            if cmd.role is not None:
                if role is None:
                    log.info("sin autorización", extra=extra)
                    await self._send(message, Reply(self.t("unauthorized", user_id=user.id)))
                    return
                ctx.require(cmd.role)
            text = (message.text or "").partition(" ")[2].strip()
            result = await cmd.handler(ctx, text)
            log.info("comando ok", extra={**extra, "role": role.label if role else None})
            await self._send(message, Reply(result) if isinstance(result, str) else result)
        except Exception as exc:  # todo fallo se convierte en un mensaje seguro y útil
            await self._send(message, Reply(self._error_text(exc, cmd.name, extra)))
        finally:
            correlation_id.reset(cid)

    async def on_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.data is None or not self._chat_allowed(update):
            return
        cid = correlation_id.set(f"u{update.update_id}")
        try:
            await self._callback(query)
        finally:
            correlation_id.reset(cid)

    async def _callback(self, query: CallbackQuery) -> None:
        token, _sep, arg = (query.data or "").partition(":")
        extra = {"user_id": query.from_user.id, "callback": arg}
        item = self.interactions.get(token)
        if item is None or item.expires < time.monotonic():
            await query.answer(self.t("expired"), show_alert=True)
            return
        if query.from_user.id != item.owner:
            await query.answer(self.t("not_yours"), show_alert=True)
            return
        role = self.store.role_of(query.from_user.id)
        if role is None or role < item.role:  # se revisa otra vez: pudo perder el rol
            self.interactions.pop(token, None)
            await query.answer(self.t("lost_permission"), show_alert=True)
            return
        if arg in {"ok", "no"}:  # las confirmaciones son de un solo uso
            self.interactions.pop(token, None)
        await query.answer()
        edit = item.edit
        try:
            reply = await item.run(arg)
            log.info("botón ok", extra=extra)
        except Exception as exc:
            reply, edit = Reply(self._error_text(exc, "callback", extra)), False
        if isinstance(query.message, Message):
            await (self._edit if edit else self._send)(query.message, reply)

    def _error_text(self, exc: Exception, cmd: str, extra: dict[str, object]) -> str:
        t = self.t
        if isinstance(exc, UserError):
            params = dict(exc.params)
            if exc.key == "usage":
                params = {"usage": t(f"cmd.{cmd}.usage"), "example": t(f"cmd.{cmd}.example")}
            log.info("error del usuario", extra={**extra, "error": exc.key})
            return t(exc.key, **params)
        if isinstance(exc, GitHubError):
            log.warning(
                "error de GitHub",
                extra={**extra, "status": exc.status, "gh_request_id": exc.request_id},
            )
            key = {
                0: "gh_network",
                401: "gh_401",
                403: "gh_403",
                404: "gh_404",
                409: "gh_409",
                410: "gh_410",
                422: "gh_422",
                429: "gh_429",
            }.get(exc.status)
            if key is None:
                key = "gh_5xx" if exc.status >= 500 else "gh_other"
            return t(key, status=exc.status, message=exc.message[:300])
        log.exception("error interno", extra=extra)
        return t("internal_error", ref=correlation_id.get())

    def _fit(self, reply: Reply) -> tuple[str, str | None, InlineKeyboardMarkup | None]:
        markup = None
        if reply.buttons:
            markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(label, callback_data=data) for label, data in row]
                    for row in reply.buttons
                ]
            )
        if len(reply.text) <= self.settings.max_message_length:
            return reply.text, ParseMode.HTML, markup
        # Cortar HTML puede romper etiquetas: mejor pasar a texto plano.
        plain = html.unescape(re.sub(r"<[^>]+>", "", reply.text))
        return plain[: self.settings.max_message_length - 1] + "…", None, markup

    async def _send(self, message: Message, reply: Reply) -> None:
        text, mode, markup = self._fit(reply)
        await message.reply_text(text, parse_mode=mode, reply_markup=markup)

    async def _edit(self, message: Message, reply: Reply) -> None:
        text, mode, markup = self._fit(reply)
        try:
            await message.edit_text(text, parse_mode=mode, reply_markup=markup)
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise


def build_application(
    settings: Settings,
    store: Store,
    gh: GitHub,
    *,
    request: BaseRequest | None = None,
    updates_request: BaseRequest | None = None,
) -> Application:  # type: ignore[type-arg]
    importlib.import_module("tgbot.commands")  # registra todos los módulos de comandos
    rt = Runtime(settings, store, gh)
    builder = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .defaults(Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True)))
    )
    if request is not None:
        builder = builder.request(request)
    if updates_request is not None:
        builder = builder.get_updates_request(updates_request)
    app = builder.build()
    app.bot_data["runtime"] = rt
    # Solo MESSAGE: editar un "/issue_new ..." no debe crear un segundo issue.
    for cmd in COMMANDS.values():
        app.add_handler(
            CommandHandler(cmd.name, partial(rt.dispatch, cmd), filters.UpdateType.MESSAGE)
        )
    app.add_handler(CallbackQueryHandler(rt.on_callback))
    return app


async def publish_commands(app: Application) -> None:  # type: ignore[type-arg]
    """Muestra la lista de comandos en el menú "/" de los clientes de Telegram."""
    rt: Runtime = app.bot_data["runtime"]
    await app.bot.set_my_commands(
        [BotCommand(name, rt.t(f"cmd.{name}.desc")[:256]) for name in COMMANDS]
    )
