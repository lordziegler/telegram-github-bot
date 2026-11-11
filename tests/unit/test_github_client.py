from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tgbot.github import GitHub, GitHubError


def client(responses: list[Any], seen: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[no-any-return]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make(responses: list[Any], seen: list[httpx.Request], sleeps: list[float]) -> GitHub:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return GitHub("ghp_x", client=client(responses, seen), sleep=sleep)


def test_get_retries_transient_errors() -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gh = make([httpx.Response(502), httpx.Response(200, json={"ok": 1})], seen, sleeps)
    assert asyncio.run(gh.get("/x")) == {"ok": 1}
    assert len(seen) == 2 and sleeps == [1.0]
    assert seen[0].headers["authorization"] == "Bearer ghp_x"


def test_post_is_not_retried_on_5xx_or_read_errors() -> None:
    for failure in (httpx.Response(502), httpx.ReadTimeout("slow")):
        seen: list[httpx.Request] = []
        gh = make([failure, httpx.Response(201, json={})], seen, [])
        with pytest.raises(GitHubError):
            asyncio.run(gh.send("POST", "/repos/o/r/issues", {"title": "t"}))
        assert len(seen) == 1  # reintentar podría duplicar el issue


def test_post_is_retried_when_nothing_was_sent() -> None:
    seen: list[httpx.Request] = []
    gh = make([httpx.ConnectError("down"), httpx.Response(201, json={"n": 1})], seen, [])
    assert asyncio.run(gh.send("POST", "/x", {})) == {"n": 1}


def test_rate_limit_honours_retry_after_within_budget() -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    limited = httpx.Response(403, headers={"retry-after": "3"}, json={"message": "secondary"})
    gh = make([limited, httpx.Response(201, json={"n": 1})], seen, sleeps)
    assert asyncio.run(gh.send("POST", "/x", {})) == {"n": 1}
    assert sleeps == [3.0]


def test_rate_limit_beyond_budget_fails_fast() -> None:
    seen: list[httpx.Request] = []
    reset = str(int(time.time()) + 3600)
    limited = httpx.Response(
        403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset},
        json={"message": "API rate limit exceeded"},
    )
    gh = make([limited], seen, [])
    with pytest.raises(GitHubError) as err:
        asyncio.run(gh.get("/x"))
    assert err.value.status == 429 and len(seen) == 1


def test_error_details_and_request_id() -> None:
    body = {"message": "Validation Failed", "errors": [{"message": "bad label"}, {"code": "x"}]}
    resp = httpx.Response(422, json=body, headers={"x-github-request-id": "ABC"})
    gh = make([resp], [], [])
    with pytest.raises(GitHubError) as err:
        asyncio.run(gh.get("/x"))
    assert err.value.message == "Validation Failed; bad label; x"
    assert err.value.request_id == "ABC"


def test_log_tail_follows_redirect_without_credentials() -> None:
    seen: list[httpx.Request] = []
    log = "\n".join(f"line {i}" for i in range(100))
    gh = make(
        [
            httpx.Response(302, headers={"location": "https://logs.example.net/blob?sig=1"}),
            httpx.Response(200, text=log),
        ],
        seen,
        [],
    )
    tail = asyncio.run(gh.log_tail("/repos/o/r/actions/jobs/1/logs", lines=3))
    assert tail == ["line 97", "line 98", "line 99"]
    assert "authorization" not in seen[1].headers


def test_github_app_mints_and_caches_installation_token() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    seen: list[httpx.Request] = []
    responses = [
        httpx.Response(201, json={"token": "ghs_installation", "expires_at": expires}),
        httpx.Response(200, json={"a": 1}),
        httpx.Response(200, json={"b": 2}),
    ]
    gh = GitHub(
        app_id="123", app_private_key=pem, installation_id=7, client=client(responses, seen)
    )

    async def two_calls() -> None:
        await gh.get("/repos/o/r")
        await gh.get("/repos/o/r/issues")

    asyncio.run(two_calls())
    assert seen[0].url.path == "/app/installations/7/access_tokens"
    app_jwt = seen[0].headers["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(app_jwt, key.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] <= 600
    assert [r.headers["authorization"] for r in seen[1:]] == ["Bearer ghs_installation"] * 2
    assert json.loads(seen[0].content or b"null") is None
