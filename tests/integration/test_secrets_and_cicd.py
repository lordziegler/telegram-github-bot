"""Secrets del repositorio y acciones de CI/CD (/workflows, cancelar, reintentar, aprobar)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Coroutine
from typing import Any

import httpx
import pytest
from conftest import ADMIN, MAINTAINER, VIEWER, harness
from nacl.public import PrivateKey, SealedBox

REPO = "/repos/octo-org/api"
VALUE = "s3cr3t válue with spaces"


def run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


def endpoints(h: Any) -> list[str]:
    return [e for e, _ in h.tg.calls]


def test_secret_set_is_encrypted_deleted_and_never_echoed(caplog: pytest.LogCaptureFixture) -> None:
    key = PrivateKey.generate()
    public_b64 = base64.b64encode(bytes(key.public_key)).decode()

    async def scenario() -> None:
        h = await harness(
            {
                ("GET", f"{REPO}/actions/secrets/public-key"): {
                    "key_id": "kid1",
                    "key": public_b64,
                },
                ("PUT", f"{REPO}/actions/secrets/DEPLOY_TOKEN"): httpx.Response(201),
            }
        )
        await h.send(ADMIN, f"/secret set deploy_token {VALUE}")
        calls = endpoints(h)
        assert calls.index("deleteMessage") < calls.index("sendMessage")  # se borra primero
        assert "crear" in h.tg.last_text() and "DEPLOY_TOKEN" in h.tg.last_text()
        assert h.gh.calls("PUT") == []  # todavía falta confirmar

        await h.press(ADMIN, h.buttons()[0])
        body = json.loads(h.gh.calls("PUT")[0].content)
        assert body["key_id"] == "kid1"
        clear = SealedBox(key).decrypt(base64.b64decode(body["encrypted_value"])).decode()
        assert clear == VALUE
        assert "guardado" in h.tg.last_text()

        # El valor no aparece en claro en ningún sitio.
        tg_out = json.dumps([p for e, p in h.tg.calls if e != "getMe"], ensure_ascii=False)
        gh_out = b"".join(r.content for r in h.gh.requests).decode(errors="replace")
        logs = json.dumps([r.__dict__ for r in caplog.records], default=str, ensure_ascii=False)
        for blob in (tg_out, gh_out, logs):
            assert VALUE not in blob

    with caplog.at_level(logging.DEBUG):
        run(scenario())


def test_secret_replace_is_announced() -> None:
    async def scenario() -> None:
        key = base64.b64encode(bytes(PrivateKey.generate().public_key)).decode()
        h = await harness(
            {
                ("GET", f"{REPO}/actions/secrets/public-key"): {"key_id": "k", "key": key},
                ("GET", f"{REPO}/actions/secrets/API_KEY"): {"name": "API_KEY"},
            }
        )
        await h.send(ADMIN, "/secret set API_KEY nuevo")
        assert "reemplazar" in h.tg.last_text()

    run(scenario())


def test_secret_value_in_a_group_is_refused_and_deleted() -> None:
    async def scenario() -> None:
        h = await harness(TELEGRAM_ALLOWED_CHAT_IDS="-100123")
        await h.send(ADMIN, "/secret set TOKEN abc", chat_id=-100123, chat_type="supergroup")
        assert "deleteMessage" in endpoints(h)
        assert "solo se envían en chat privado" in h.tg.last_text()
        assert h.gh.requests == []

    run(scenario())


def test_secret_typos_still_delete_the_value() -> None:
    async def scenario() -> None:
        h = await harness()
        await h.send(ADMIN, "/secret sett TOKEN abc")
        assert "deleteMessage" in endpoints(h) and "Uso:" in h.tg.last_text()
        await h.send(ADMIN, "/secret set GITHUB_TOKEN abc")
        assert "no es un nombre de secret válido" in h.tg.last_text()

    run(scenario())


def test_secret_list_delete_and_admin_only() -> None:
    async def scenario() -> None:
        listing = {
            "total_count": 1,
            "secrets": [
                {
                    "name": "API_KEY",
                    "created_at": "2026-11-01T00:00:00Z",
                    "updated_at": "2026-11-02T00:00:00Z",
                }
            ],
        }
        h = await harness(
            {
                ("GET", f"{REPO}/actions/secrets"): listing,
                ("DELETE", f"{REPO}/actions/secrets/API_KEY"): httpx.Response(204),
            }
        )
        await h.send(MAINTAINER, "/secret")
        assert "requiere el rol <b>admin</b>" in h.tg.last_text()
        await h.send(ADMIN, "/secret")
        assert "API_KEY" in h.tg.last_text()
        await h.send(ADMIN, "/secret delete api_key")
        assert h.gh.calls("DELETE") == []
        await h.press(ADMIN, h.buttons()[0])
        assert h.gh.calls("DELETE")[0].url.path == f"{REPO}/actions/secrets/API_KEY"

    run(scenario())


def test_workflows_list() -> None:
    async def scenario() -> None:
        h = await harness(
            {
                ("GET", f"{REPO}/actions/workflows"): {
                    "workflows": [
                        {
                            "name": "CI",
                            "path": ".github/workflows/ci.yml",
                            "state": "active",
                            "html_url": "https://github.com/octo-org/api/actions/workflows/ci.yml",
                        }
                    ]
                }
            }
        )
        await h.send(VIEWER, "/workflows")
        assert "<code>ci.yml</code>" in h.tg.last_text()

    run(scenario())


def _run(status: str, conclusion: str | None) -> dict[str, Any]:
    return {
        "id": 77,
        "run_number": 5,
        "name": "Desplegar",
        "status": status,
        "conclusion": conclusion,
        "html_url": "https://github.com/x/actions/runs/77",
        "head_branch": "main",
        "event": "workflow_run",
        "created_at": "2026-11-11T10:00:00Z",
    }


def _routes(run_: dict[str, Any], extra: dict[tuple[str, str], Any]) -> dict[tuple[str, str], Any]:
    return {
        ("GET", f"{REPO}/actions/workflows/deploy.yml/runs"): {"workflow_runs": [run_]},
        ("GET", f"{REPO}/actions/runs/77"): run_,
        ("GET", f"{REPO}/actions/runs/77/jobs"): {"jobs": []},
        **extra,
    }


def test_admin_approves_a_pending_deployment_once() -> None:
    async def scenario() -> None:
        h = await harness(
            _routes(
                _run("waiting", None),
                {
                    ("GET", f"{REPO}/actions/runs/77/pending_deployments"): [
                        {
                            "environment": {"id": 9, "name": "production"},
                            "current_user_can_approve": True,
                        }
                    ],
                    ("POST", f"{REPO}/actions/runs/77/pending_deployments"): [],
                },
            )
        )
        await h.send(ADMIN, "/runs deploy.yml")
        await h.press(ADMIN, h.buttons()[0])  # "#5": detalle
        approve, _reject, _cancel = h.buttons()  # aprobar, rechazar, cancelar
        await h.press(ADMIN, approve)
        body = json.loads(h.gh.calls("POST")[0].content)
        assert body["environment_ids"] == [9] and body["state"] == "approved" and body["comment"]
        assert "aprobado" in h.tg.last_text()
        await h.press(ADMIN, approve)  # un solo uso
        assert "caducó" in h.tg.alerts()[-1]
        assert len(h.gh.calls("POST")) == 1

    run(scenario())


def test_viewer_sees_no_cicd_actions_and_admin_reruns_failures() -> None:
    async def scenario() -> None:
        h = await harness(
            _routes(
                _run("completed", "failure"),
                {("POST", f"{REPO}/actions/runs/77/rerun-failed-jobs"): httpx.Response(201)},
            )
        )
        await h.send(VIEWER, "/runs deploy.yml")
        await h.press(VIEWER, h.buttons()[0])
        assert h.buttons() == []

        await h.send(ADMIN, "/runs deploy.yml")
        await h.press(ADMIN, h.buttons()[0])
        (rerun,) = h.buttons()
        await h.press(ADMIN, rerun)
        assert h.gh.calls("POST")[0].url.path == f"{REPO}/actions/runs/77/rerun-failed-jobs"

    run(scenario())


def test_approve_and_reject_exclude_each_other() -> None:
    async def scenario() -> None:
        pending = [
            {"environment": {"id": 9, "name": "production"}, "current_user_can_approve": True}
        ]
        h = await harness(
            _routes(
                _run("waiting", None),
                {
                    ("GET", f"{REPO}/actions/runs/77/pending_deployments"): pending,
                    ("POST", f"{REPO}/actions/runs/77/pending_deployments"): [],
                },
            )
        )
        await h.send(ADMIN, "/runs deploy.yml")
        await h.press(ADMIN, h.buttons()[0])
        approve, reject, _cancel = h.buttons()
        await h.press(ADMIN, approve)
        await h.press(ADMIN, reject)
        assert "caducó" in h.tg.alerts()[-1]
        assert len(h.gh.calls("POST")) == 1

    run(scenario())


def test_waiting_deploy_the_token_cannot_approve_is_explained() -> None:
    async def scenario() -> None:
        pending = [
            {"environment": {"id": 9, "name": "production"}, "current_user_can_approve": False}
        ]
        h = await harness(
            _routes(
                _run("waiting", None),
                {("GET", f"{REPO}/actions/runs/77/pending_deployments"): pending},
            )
        )
        await h.send(ADMIN, "/runs deploy.yml")
        await h.press(ADMIN, h.buttons()[0])
        assert "no es revisor" in h.tg.last_text()
        assert len(h.buttons()) == 1  # solo «Cancelar»

    run(scenario())
