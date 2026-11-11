from __future__ import annotations

import json
import logging
import sys

import pytest
from conftest import ADMIN, BOT_TOKEN, MAINTAINER, VIEWER, make_settings

from tgbot.domain import Role, UserError
from tgbot.log import JsonFormatter, correlation_id
from tgbot.store import Store


def record(msg: str, *args: object, exc: bool = False) -> logging.LogRecord:
    exc_info = None
    if exc:
        try:
            raise RuntimeError(f"boom {BOT_TOKEN}")
        except RuntimeError:
            exc_info = sys.exc_info()
    return logging.LogRecord("t", logging.INFO, "f", 1, msg, args, exc_info)


def test_secrets_never_reach_the_output() -> None:
    fmt = JsonFormatter([BOT_TOKEN, "my-webhook-secret"])
    leaks = [
        record("POST https://api.telegram.org/bot%s/getUpdates", BOT_TOKEN),
        record("secret is my-webhook-secret"),
        record("installation ghs_123_eyJhbGciOiJSUzI1NiJ9.payload.sig"),
        record("pat github_pat_11ABCDEFGHIJKLMNOPQRSTUV_xyz"),
        record("key -----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----"),
        record("failed", exc=True),
    ]
    for r in leaks:
        out = fmt.format(r)
        assert "[REDACTED]" in out, out
        for secret in (BOT_TOKEN, "my-webhook-secret", "ghs_123", "github_pat_11", "MIIE"):
            assert secret not in out


def test_json_shape_and_correlation_id() -> None:
    token = correlation_id.set("u77")
    try:
        r = record("command ok")
        r.user_id = 5
        payload = json.loads(JsonFormatter().format(r))
    finally:
        correlation_id.reset(token)
    assert payload["cid"] == "u77" and payload["user_id"] == 5 and payload["level"] == "INFO"


@pytest.fixture
def store() -> Store:
    return Store(make_settings(DEFAULT_REPOSITORIES="octo-org/api"))


def test_roles_overlay_env(store: Store) -> None:
    assert store.role_of(ADMIN) is Role.ADMIN
    assert store.role_of(VIEWER) is Role.VIEWER  # por defecto para usuarios del env
    assert store.role_of(424242) is None
    store.set_role(VIEWER, Role.MAINTAINER)
    assert store.role_of(VIEWER) is Role.MAINTAINER
    with pytest.raises(UserError, match="env_admin_locked"):
        store.set_role(ADMIN, Role.VIEWER)
    with pytest.raises(UserError, match="env_user_locked"):
        store.deny(MAINTAINER)
    store.allow(424242)
    assert store.role_of(424242) is Role.VIEWER
    store.deny(424242)
    assert store.role_of(424242) is None


def test_repos_and_selection(store: Store) -> None:
    store.add_repo("octo-org/web")
    with pytest.raises(UserError, match="repo_exists"):
        store.add_repo("OCTO-ORG/API")
    assert store.repos() == ["octo-org/api", "octo-org/web"]
    assert store.selected(VIEWER) == "octo-org/api"
    store.select(VIEWER, "octo-org/web")
    assert store.selected(VIEWER) == "octo-org/web"
    store.remove_repo("Octo-Org/Web")
    # vuelve al primero si la selección ya no existe
    assert store.selected(VIEWER) == "octo-org/api"
    with pytest.raises(UserError, match="env_repo_locked"):
        store.remove_repo("octo-org/api")


def test_store_persists_to_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(DATABASE_URL=f"sqlite:///{tmp_path}/sub/bot.db")
    Store(settings).set_role(777, Role.MAINTAINER)
    assert Store(settings).role_of(777) is Role.MAINTAINER
