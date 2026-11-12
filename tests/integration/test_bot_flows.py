"""De extremo a extremo con la Application real de python-telegram-bot; Telegram y GitHub
son falsos."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import httpx
from conftest import ADMIN, MAINTAINER, STRANGER, VIEWER, Harness, fixture, harness

from tgbot.domain import Role

REPO = "/repos/octo-org/api"


def run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


def test_stranger_gets_their_id_and_github_is_never_called() -> None:
    async def scenario() -> None:
        h = await harness()
        await h.send(STRANGER, "/issues")
        assert "No tienes acceso" in h.tg.last_text()
        assert f"/admin allow {STRANGER}" in h.tg.last_text()
        await h.send(STRANGER, "/start")
        assert str(STRANGER) in h.tg.last_text()
        assert h.gh.requests == []

    run(scenario())


def test_viewer_reads_but_cannot_write() -> None:
    async def scenario() -> None:
        h = await harness({("GET", f"{REPO}/issues"): fixture("issues.json")})
        await h.send(VIEWER, "/issues open label:bug")
        text = h.tg.last_text()
        assert "#5" in text and "Login returns &lt;500&gt;" in text  # HTML escapado
        assert "#6" not in text  # /issues descarta los pull requests
        params = h.gh.requests[-1].url.params
        assert params["state"] == "open" and params["labels"] == "bug"

        await h.send(VIEWER, "/issue_new Broken | details")
        assert "requiere el rol <b>maintainer</b>" in h.tg.last_text()
        assert h.gh.calls("POST") == []

    run(scenario())


def test_maintainer_creates_issue_and_comments() -> None:
    async def scenario() -> None:
        created = {
            "number": 7,
            "html_url": "https://github.com/octo-org/api/issues/7",
            "title": "Broken login",
        }
        h = await harness(
            {
                ("POST", f"{REPO}/issues"): created,
                ("POST", f"{REPO}/issues/7/comments"): {"html_url": "https://x/c/1"},
            }
        )
        await h.send(MAINTAINER, "/issue_new Broken login | Steps:\nclick")
        body = json.loads(h.gh.calls("POST")[0].content)
        assert body == {"title": "Broken login", "body": "Steps:\nclick"}
        assert "Issue creado" in h.tg.last_text()

        await h.send(MAINTAINER, "/comment 7 Reproduced")
        assert json.loads(h.gh.calls("POST")[1].content) == {"body": "Reproduced"}

        await h.send(MAINTAINER, "/comment 7")
        assert "Uso:" in h.tg.last_text()

    run(scenario())


def test_close_needs_confirmation_from_the_same_user() -> None:
    async def scenario() -> None:
        issue = fixture("issues.json")[0]
        h = await harness(
            {
                ("GET", f"{REPO}/issues/5"): issue,
                ("PATCH", f"{REPO}/issues/5"): {**issue, "state": "closed"},
            }
        )
        await h.send(MAINTAINER, "/close 5")
        assert "Vas a <b>cerrar</b> #5" in h.tg.last_text()
        ok, _cancel = h.buttons()
        assert h.gh.calls("PATCH") == []

        await h.press(VIEWER, ok)  # otra persona del chat
        assert "Solo quien lanzó" in h.tg.alerts()[-1]
        assert h.gh.calls("PATCH") == []

        await h.press(MAINTAINER, ok)
        assert json.loads(h.gh.calls("PATCH")[0].content) == {"state": "closed"}
        assert "cerrado" in h.tg.last_text()

        await h.press(MAINTAINER, ok)  # un solo uso
        assert "caducó" in h.tg.alerts()[-1]
        assert len(h.gh.calls("PATCH")) == 1

    run(scenario())


def test_confirmation_is_refused_after_losing_the_role() -> None:
    async def scenario() -> None:
        h = await harness({("GET", f"{REPO}/issues/5"): fixture("issues.json")[0]})
        h.store.set_role(MAINTAINER, Role.MAINTAINER)
        await h.send(MAINTAINER, "/close 5")
        ok, _ = h.buttons()
        h.store.set_role(MAINTAINER, Role.VIEWER)
        await h.press(MAINTAINER, ok)
        assert "Ya no tienes el rol" in h.tg.alerts()[-1]
        assert h.gh.calls("PATCH") == []

    run(scenario())


def test_admin_manages_users_and_repos() -> None:
    async def scenario() -> None:
        h = await harness({("GET", "/repos/octo-org/web"): {"full_name": "octo-org/web"}})
        await h.send(ADMIN, "/admin allow 5555")
        assert h.store.role_of(5555) is Role.VIEWER

        await h.send(ADMIN, "/admin role 5555 maintainer")
        await h.press(ADMIN, h.buttons()[0])
        assert h.store.role_of(5555) is Role.MAINTAINER

        await h.send(ADMIN, f"/admin deny {ADMIN}")
        assert "No puedes revocarte" in h.tg.last_text()
        await h.send(ADMIN, f"/admin role {ADMIN} viewer")
        await h.press(ADMIN, h.buttons()[0])
        assert "admin por configuración" in h.tg.last_text()
        assert h.store.role_of(ADMIN) is Role.ADMIN

        await h.send(ADMIN, "/admin repo add octo-org/web")
        assert h.store.repos() == ["octo-org/api", "octo-org/web"]
        await h.send(ADMIN, "/admin repo remove octo-org/api")
        await h.press(ADMIN, h.buttons()[0])
        assert "DEFAULT_REPOSITORIES" in h.tg.last_text()

        await h.send(MAINTAINER, "/admin allow 1")
        assert "requiere el rol <b>admin</b>" in h.tg.last_text()

    run(scenario())


def test_github_errors_become_actionable_messages() -> None:
    async def scenario() -> None:
        h = await harness(
            {
                ("GET", f"{REPO}/pulls/3"): [
                    httpx.Response(502),
                    httpx.Response(502),
                    httpx.Response(502),
                    httpx.Response(502),
                ],
                ("POST", f"{REPO}/pulls/3/reviews"): httpx.Response(
                    422,
                    json={"message": "Unprocessable", "errors": ["Can not approve your own PR"]},
                ),
            }
        )
        await h.send(VIEWER, "/issue 404")
        assert "No encontrado en GitHub" in h.tg.last_text()
        await h.send(VIEWER, "/pr 3")
        assert "HTTP 502" in h.tg.last_text()
        assert len(h.gh.calls("GET")) == 1 + 4  # el 404 + 4 intentos contra el 502
        await h.send(MAINTAINER, "/review 3 comment looks good")
        assert "Can not approve your own PR" in h.tg.last_text()

    run(scenario())


def test_ignored_contexts() -> None:
    async def scenario() -> None:
        h = await harness()
        await h.send(ADMIN, "/whoami", chat_id=-100123, chat_type="supergroup")
        assert h.tg.sent() == []  # grupo fuera de TELEGRAM_ALLOWED_CHAT_IDS
        edited = {
            "update_id": 500,
            "edited_message": {
                "message_id": 1,
                "date": 0,
                "edit_date": 1,
                "chat": {"id": ADMIN, "type": "private"},
                "from": {"id": ADMIN, "is_bot": False, "first_name": "A"},
                "text": "/whoami",
                "entities": [{"type": "bot_command", "offset": 0, "length": 7}],
            },
        }
        from telegram import Update

        await h.app.process_update(Update.de_json(edited, h.app.bot))
        assert h.tg.sent() == []  # editar nunca vuelve a ejecutar un comando

    run(scenario())


def test_allowed_group_works() -> None:
    async def scenario() -> None:
        h = await harness(TELEGRAM_ALLOWED_CHAT_IDS="-100123")
        await h.send(ADMIN, "/whoami", chat_id=-100123, chat_type="supergroup")
        assert "admin" in h.tg.last_text()

    run(scenario())


def test_rate_limit_per_user() -> None:
    async def scenario() -> None:
        h = await harness(RATE_LIMIT_REQUESTS_PER_MINUTE="2")
        for _ in range(3):
            await h.send(ADMIN, "/whoami")
        assert "Demasiadas solicitudes" in h.tg.last_text()
        assert sum("Demasiadas" in str(p["text"]) for p in h.tg.sent()) == 1

    run(scenario())


def test_pagination_buttons_belong_to_the_requester() -> None:
    async def scenario() -> None:
        page1 = httpx.Response(
            200,
            json=fixture("issues.json"),
            headers={"link": f'<https://api.github.com{REPO}/issues?page=2>; rel="next"'},
        )
        h = await harness({("GET", f"{REPO}/issues"): [page1, httpx.Response(200, json=[])]})
        await h.send(VIEWER, "/issues")
        (next_page,) = h.buttons()
        assert next_page.endswith(":2")
        await h.press(MAINTAINER, next_page)
        assert "Solo quien lanzó" in h.tg.alerts()[-1]
        await h.press(VIEWER, next_page)
        assert "página 2" in h.tg.last_text() and h.buttons() == [next_page[:-1] + "1"]

    run(scenario())


def test_help_fits_in_one_message_and_every_command_is_translated() -> None:
    async def scenario() -> None:
        h = await harness()
        await h.send(ADMIN, "/help")
        text = h.tg.last_text()
        assert "<code>" in text  # sigue en HTML: no se desbordó al texto plano
        from tgbot.bot import COMMANDS

        rt = h.app.bot_data["runtime"]
        for name in COMMANDS:
            for part in ("desc", "usage", "example"):
                assert f"cmd.{name}.{part}" in rt.t.strings, f"missing cmd.{name}.{part}"

    run(scenario())


def test_ci_falls_back_to_actions_runs_for_fine_grained_pats() -> None:
    async def scenario() -> None:
        sha = "a" * 40
        h = await harness(
            {
                ("GET", f"{REPO}/pulls/3"): {"head": {"sha": sha}},
                ("GET", f"{REPO}/commits/{sha}/status"): {
                    "statuses": [
                        {"state": "success", "context": "lint", "target_url": "https://ci/1"}
                    ]
                },
                ("GET", f"{REPO}/commits/{sha}/check-runs"): httpx.Response(
                    403, json={"message": "Resource not accessible by personal access token"}
                ),
                ("GET", f"{REPO}/actions/runs"): {
                    "workflow_runs": [
                        {
                            "name": "tests",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/r/1",
                        }
                    ]
                },
            }
        )
        await h.send(VIEWER, "/ci 3")
        text = h.tg.last_text()
        assert "❌ 1 · ⏳ 0 · ✅ 1" in text
        assert text.index("tests") < text.index("lint")  # primero los fallos

    run(scenario())


def test_run_workflow_requires_admin_and_confirmation() -> None:
    async def scenario() -> None:
        h = await harness(
            {
                ("GET", f"{REPO}/actions/workflows/deploy.yml"): {
                    "name": "Deploy",
                    "state": "active",
                    "path": ".github/workflows/deploy.yml",
                },
                ("GET", REPO): fixture("repo.json"),
                ("POST", f"{REPO}/actions/workflows/deploy.yml/dispatches"): {
                    "workflow_run_id": 1,
                    "run_url": "https://api/x",
                    "html_url": "https://github.com/octo-org/api/actions/runs/1",
                },
            }
        )
        await h.send(MAINTAINER, "/run deploy.yml env=prod")
        assert "requiere el rol <b>admin</b>" in h.tg.last_text()
        await h.send(ADMIN, '/run deploy.yml env=prod note="two words"')
        assert "<code>main</code>" in h.tg.last_text()
        await h.press(ADMIN, h.buttons()[0])
        body = json.loads(h.gh.calls("POST")[0].content)
        assert body == {"ref": "main", "inputs": {"env": "prod", "note": "two words"}}
        assert "actions/runs/1" in h.tg.last_text()

    run(scenario())


def test_unexpected_errors_hide_details() -> None:
    async def scenario() -> None:
        h: Harness = await harness({("GET", f"{REPO}/issues/1"): {"unexpected": "shape"}})
        await h.send(VIEWER, "/issue 1")
        assert "Error interno" in h.tg.last_text()
        assert "KeyError" not in h.tg.last_text() and "Traceback" not in h.tg.last_text()

    run(scenario())


def test_detail_status_is_singular_and_text_may_start_on_a_new_line() -> None:
    async def scenario() -> None:
        h = await harness(
            {
                ("GET", f"{REPO}/issues/5"): fixture("issues.json")[0],
                ("POST", f"{REPO}/issues/5/comments"): {"html_url": "https://x/c/2"},
                ("POST", f"{REPO}/issues/5/labels"): [{"name": "bug"}],
            }
        )
        await h.send(VIEWER, "/issue 5")
        assert "Estado: abierto ·" in h.tg.last_text()
        await h.send(MAINTAINER, "/comment 5\nprimera línea\nsegunda")
        assert json.loads(h.gh.calls("POST")[0].content) == {"body": "primera línea\nsegunda"}
        await h.send(MAINTAINER, "/label 5\nbug")
        assert json.loads(h.gh.calls("POST")[1].content) == {"labels": ["bug"]}
        await h.send(MAINTAINER, "/comment")
        assert "Uso:" in h.tg.last_text()

    run(scenario())


def test_button_presses_are_rate_limited() -> None:
    async def scenario() -> None:
        page = httpx.Response(200, json=[], headers={"link": '<x?page=2>; rel="next"'})
        h = await harness({("GET", f"{REPO}/issues"): page}, RATE_LIMIT_REQUESTS_PER_MINUTE="3")
        await h.send(VIEWER, "/issues")
        (next_page,) = h.buttons()
        for _ in range(3):
            await h.press(VIEWER, next_page)
        assert "Demasiadas solicitudes" in h.tg.alerts()[-1]
        assert len(h.gh.calls("GET")) == 3  # el comando + 2 pulsaciones; la tercera, bloqueada

    run(scenario())


def test_commands_are_listed_public_first() -> None:
    async def scenario() -> None:
        from tgbot.bot import publish_commands

        h = await harness()
        await publish_commands(h.app)
        (params,) = [p for e, p in h.tg.calls if e == "setMyCommands"]
        names = [c["command"] for c in params["commands"]]
        assert names[:3] == ["start", "help", "whoami"] and names[-1] in {"admin", "secret"}
        await h.send(ADMIN, "/help")
        assert h.tg.last_text().index("/start") < h.tg.last_text().index("/run")

    run(scenario())
