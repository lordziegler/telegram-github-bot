"""Dobles de prueba: una Bot API de Telegram falsa (BaseRequest) y un GitHub falso
(httpx.MockTransport)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from telegram import Update
from telegram.ext import Application
from telegram.request import BaseRequest, RequestData

from tgbot.bot import build_application
from tgbot.config import Settings, load_settings
from tgbot.domain import Role
from tgbot.github import GitHub
from tgbot.store import Store

FIXTURES = Path(__file__).parent / "integration" / "fixtures"
BOT_TOKEN = "123456:TEST-not-a-real-token-abcdefghijklmnop"
ADMIN, MAINTAINER, VIEWER, STRANGER = 1001, 1002, 1003, 9999


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def make_settings(**overrides: str) -> Settings:
    env = {
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "TELEGRAM_ADMIN_USER_IDS": str(ADMIN),
        "TELEGRAM_ALLOWED_USER_IDS": f"{MAINTAINER},{VIEWER}",
        "GITHUB_TOKEN": "ghp_testtoken0000000000000000000000000000",
        "DEFAULT_REPOSITORIES": "octo-org/api",
        "DEFAULT_TIMEZONE": "America/Bogota",
    }
    env.update(overrides)
    return load_settings(env)


class FakeTelegram(BaseRequest):
    """Registra las llamadas a la Bot API y responde como lo haría Telegram."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def read_timeout(self) -> float:
        return 1.0

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(
        self, url: str, method: str, request_data: RequestData | None = None, *args: Any, **kw: Any
    ) -> tuple[int, bytes]:
        endpoint = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((endpoint, params))
        result: Any = True
        if endpoint == "getMe":
            result = {"id": 42, "is_bot": True, "first_name": "Bot", "username": "test_bot"}
        elif endpoint in {"sendMessage", "editMessageText"}:
            chat = params.get("chat_id", 1)
            result = {
                "message_id": 99,
                "date": 0,
                "chat": {"id": chat, "type": "private"},
                "text": params.get("text", ""),
            }
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def sent(self) -> list[dict[str, Any]]:
        return [p for e, p in self.calls if e in {"sendMessage", "editMessageText"}]

    def last_text(self) -> str:
        return str(self.sent()[-1]["text"])

    def alerts(self) -> list[str]:
        return [str(p.get("text")) for e, p in self.calls if e == "answerCallbackQuery"]


@dataclass
class FakeGitHub:
    """Tabla de rutas: (MÉTODO, ruta) -> cuerpo JSON, un httpx.Response o una lista de ellos."""

    routes: dict[tuple[str, str], Any] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        value = self.routes.get((request.method, request.url.path))
        if isinstance(value, list) and value and isinstance(value[0], httpx.Response):
            value = value.pop(0) if len(value) > 1 else value[0]
        if value is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    def calls(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method]


async def no_sleep(_: float) -> None:
    pass


@dataclass
class Harness:
    app: Application  # type: ignore[type-arg]
    tg: FakeTelegram
    gh: FakeGitHub
    store: Store
    next_id: int = 1

    async def send(
        self, user_id: int, text: str, chat_id: int | None = None, chat_type: str = "private"
    ) -> None:
        command = text.split()[0]
        data = {
            "update_id": self._id(),
            "message": {
                "message_id": self.next_id,
                "date": 0,
                "chat": {"id": chat_id or user_id, "type": chat_type},
                "from": {"id": user_id, "is_bot": False, "first_name": "Ana"},
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(command)}],
            },
        }
        await self.app.process_update(Update.de_json(data, self.app.bot))

    async def press(self, user_id: int, data: str) -> None:
        update = {
            "update_id": self._id(),
            "callback_query": {
                "id": str(self.next_id),
                "from": {"id": user_id, "is_bot": False, "first_name": "Ana"},
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": 99,
                    "date": 0,
                    "chat": {"id": user_id, "type": "private"},
                    "text": "x",
                },
            },
        }
        await self.app.process_update(Update.de_json(update, self.app.bot))

    def buttons(self) -> list[str]:
        """callback_data del teclado inline del último mensaje enviado o editado."""
        markup = self.tg.sent()[-1].get("reply_markup") or {}
        return [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id


async def harness(routes: dict[tuple[str, str], Any] | None = None, **env: str) -> Harness:
    settings = make_settings(**env)
    store = Store(settings)
    store.set_role(MAINTAINER, Role.MAINTAINER)  # los usuarios del env empiezan como viewer
    fake_gh = FakeGitHub(routes or {})
    gh = GitHub(
        "ghp_testtoken0000000000000000000000000000",
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake_gh)),
        sleep=no_sleep,
    )
    tg = FakeTelegram()
    app = build_application(settings, store, gh, request=tg, updates_request=FakeTelegram())
    await app.initialize()
    return Harness(app, tg, fake_gh, store)
