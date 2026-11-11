from __future__ import annotations

import pytest
from conftest import BOT_TOKEN, make_settings

from tgbot.config import ConfigError, load_settings
from tgbot.domain import (
    RateLimiter,
    Role,
    UserError,
    parse_ci_target,
    parse_inputs,
    parse_ref,
    parse_repo,
)


def test_empty_env_lists_every_missing_variable() -> None:
    with pytest.raises(ConfigError) as err:
        load_settings({})
    text = str(err.value)
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_USER_IDS", "GITHUB_TOKEN"):
        assert name in text


def test_valid_env() -> None:
    s = make_settings(DEFAULT_REPOSITORIES=" a/b , c/d,a/b ", DATABASE_URL="sqlite:///data/x.db")
    assert s.repositories == ("a/b", "c/d")
    assert s.database_path == "data/x.db"
    assert s.timezone.key == "America/Bogota"
    assert s.rate_limit_per_minute == 30 and s.max_message_length == 4000


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"DEFAULT_REPOSITORIES": "not-a-repo"}, "DEFAULT_REPOSITORIES"),
        ({"TELEGRAM_WEBHOOK_URL": "https://bot.example.com/hook"}, "TELEGRAM_WEBHOOK_SECRET"),
        ({"TELEGRAM_WEBHOOK_URL": "http://x/hook", "TELEGRAM_WEBHOOK_SECRET": "s"}, "https"),
        ({"GITHUB_APP_ID": "1"}, "juntos"),
        ({"TELEGRAM_ALLOWED_USER_IDS": "12,abc"}, "TELEGRAM_ALLOWED_USER_IDS"),
        ({"DEFAULT_TIMEZONE": "Mars/Base"}, "DEFAULT_TIMEZONE"),
        ({"DATABASE_URL": "postgres://x"}, "sqlite"),
        ({"TELEGRAM_BOT_TOKEN": "nope"}, "BotFather"),
        ({"MAX_MESSAGE_LENGTH": "9000"}, "MAX_MESSAGE_LENGTH"),
    ],
)
def test_invalid_values_are_reported(overrides: dict[str, str], expected: str) -> None:
    with pytest.raises(ConfigError, match=expected):
        make_settings(**overrides)


def test_github_app_wins_over_pat() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----"
    s = make_settings(GITHUB_APP_ID="1", GITHUB_APP_PRIVATE_KEY=pem, GITHUB_APP_INSTALLATION_ID="2")
    assert s.uses_github_app and s.github_token is None
    assert s.github_app_private_key == pem.replace("\\n", "\n")
    assert BOT_TOKEN in s.secrets() and s.github_app_private_key in s.secrets()


@pytest.mark.parametrize("bad", ["o/..", "o/.", "../x", "o/r/x", "-o/r", "o r/x", "o/" + "r" * 101])
def test_parse_repo_rejects_traversal_and_junk(bad: str) -> None:
    with pytest.raises(UserError):
        parse_repo(bad)


def test_parse_repo_accepts_real_names() -> None:
    assert parse_repo(" octo-org/my.repo_2 ") == "octo-org/my.repo_2"


def test_parse_ci_target() -> None:
    assert parse_ci_target("#42") == 42
    assert parse_ci_target("ABCDEF1") == "abcdef1"
    assert parse_ci_target("1234567") == "1234567"  # 7 o más dígitos: un SHA
    with pytest.raises(UserError):
        parse_ci_target("main")


@pytest.mark.parametrize("bad", ["../x", "a..b", "/main", "main/", "a//b", "x y"])
def test_parse_ref_rejects(bad: str) -> None:
    with pytest.raises(UserError):
        parse_ref(bad)


def test_parse_inputs() -> None:
    assert parse_inputs(["a=1", "b_c=x=y"]) == {"a": "1", "b_c": "x=y"}
    with pytest.raises(UserError):
        parse_inputs(["noequals"])
    with pytest.raises(UserError):
        parse_inputs([f"k{i}=v" for i in range(26)])


def test_rate_limiter_sliding_window() -> None:
    now = [0.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert rl.allow("u", 2) and rl.allow("u", 2) and not rl.allow("u", 2)
    assert rl.allow("other", 2)
    now[0] = 60.0
    assert rl.allow("u", 2)
    now[0] = 200.0
    rl.prune()
    assert rl._hits == {}


def test_role_order_and_parse() -> None:
    assert Role.ADMIN > Role.MAINTAINER > Role.VIEWER
    assert Role.parse(" Maintainer ") is Role.MAINTAINER
    with pytest.raises(UserError):
        Role.parse("owner")
