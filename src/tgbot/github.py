"""Thin async GitHub REST client: auth (PAT or App), retries with backoff, error mapping."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx

API = "https://api.github.com"
log = logging.getLogger(__name__)
Json = Any  # GitHub JSON payloads are consumed as plain dicts/lists


class GitHubError(Exception):
    def __init__(self, status: int, message: str, request_id: str | None = None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status  # 0 = network error, 429 = rate limited (also for 403 rate limits)
        self.message = message
        self.request_id = request_id


class GitHub:
    def __init__(
        self,
        token: str | None = None,
        *,
        app_id: str | None = None,
        app_private_key: str | None = None,
        installation_id: int | None = None,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_wait: float = 20.0,
    ) -> None:
        self._token = token
        self._app = (app_id, app_private_key, installation_id) if app_id else None
        self._installation_token: tuple[str, float] | None = None
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._sleep = sleep
        self._max_wait = max_wait

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- auth ---------------------------------------------------------------------------------

    async def _auth_header(self) -> str:
        if self._app is None:
            return f"Bearer {self._token}"
        cached = self._installation_token
        if cached and cached[1] - time.time() > 300:
            return f"Bearer {cached[0]}"
        import jwt  # optional extra: telegram-github-bot[github-app]

        app_id, key, installation = self._app
        assert key is not None  # noqa: S101 - guaranteed by config validation
        now = int(time.time())
        app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, key, "RS256")
        resp = await self._send(
            "POST", f"/app/installations/{installation}/access_tokens", f"Bearer {app_jwt}"
        )
        data = resp.json()
        expires = datetime.fromisoformat(data["expires_at"]).timestamp()
        self._installation_token = (data["token"], expires)
        return f"Bearer {data['token']}"

    # -- transport ----------------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Json = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        return await self._send(
            method, path, await self._auth_header(), params, json, follow_redirects
        )

    async def _send(
        self,
        method: str,
        path: str,
        auth: str,
        params: dict[str, Any] | None = None,
        json: Json = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        headers = {
            "Authorization": auth,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "telegram-github-bot",
        }
        idempotent = method in {"GET", "HEAD", "PUT", "DELETE"}
        waited = 0.0
        for attempt in range(4):
            try:
                resp = await self._client.request(
                    method,
                    API + path,
                    params=params,
                    json=json,
                    headers=headers,
                    follow_redirects=follow_redirects,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached GitHub: safe to retry any method.
                delay, error = 2.0**attempt, GitHubError(0, str(exc) or type(exc).__name__)
            except httpx.TransportError as exc:
                # The request may have been processed: never retry writes (duplicate issues).
                if not idempotent:
                    raise GitHubError(0, str(exc) or type(exc).__name__) from exc
                delay, error = 2.0**attempt, GitHubError(0, str(exc) or type(exc).__name__)
            else:
                if resp.status_code < 400:
                    return resp
                error = _error(resp)
                if error.status == 429:
                    delay = _rate_limit_delay(resp)
                elif resp.status_code in {500, 502, 503, 504} and idempotent:
                    delay = 2.0**attempt
                else:
                    raise error
            if attempt == 3 or waited + delay > self._max_wait:
                raise error
            log.warning("github retry", extra={"status": error.status, "delay": delay})
            waited += delay
            await self._sleep(delay)
        raise AssertionError("unreachable")

    # -- helpers ------------------------------------------------------------------------------

    async def get(self, path: str, **params: Any) -> Json:
        return (await self.request("GET", path, params=params or None)).json()

    async def page(
        self, path: str, page: int, per_page: int = 10, **params: Any
    ) -> tuple[Json, bool]:
        """One page of a list endpoint and whether a next page exists (Link header)."""
        resp = await self.request(
            "GET", path, params={**params, "per_page": per_page, "page": page}
        )
        return resp.json(), 'rel="next"' in resp.headers.get("link", "")

    async def send(self, method: str, path: str, body: Json = None) -> Json:
        resp = await self.request(method, path, json=body)
        return resp.json() if resp.content else None

    async def log_tail(self, path: str, lines: int = 25) -> list[str]:
        """Last lines of a plain-text log. GitHub answers 302 to a short-lived URL on another
        host; httpx drops the Authorization header on cross-origin redirects."""
        tail: deque[str] = deque(maxlen=lines)
        auth = await self._auth_header()
        resp = await self._send("GET", path, auth, follow_redirects=False)
        location = resp.headers.get("location")
        if resp.status_code != 302 or not location:
            return resp.text.splitlines()[-lines:]
        async with self._client.stream("GET", location) as stream:
            if stream.status_code >= 400:
                raise GitHubError(stream.status_code, "log download failed")
            async for line in stream.aiter_lines():
                tail.append(line)
        return list(tail)


def _error(resp: httpx.Response) -> GitHubError:
    try:
        body = resp.json()
        message = str(body.get("message", "")) if isinstance(body, dict) else ""
        details = body.get("errors") if isinstance(body, dict) else None
        if isinstance(details, list):
            extra = [
                str(d.get("message") or d.get("code", "")) if isinstance(d, dict) else str(d)
                for d in details
            ]
            message = "; ".join(filter(None, [message, *extra]))
    except ValueError:
        message = resp.reason_phrase
    status = resp.status_code
    if status == 403 and (
        resp.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in resp.headers
        or "rate limit" in message.lower()
    ):
        status = 429
    return GitHubError(
        status, message or resp.reason_phrase, resp.headers.get("x-github-request-id")
    )


def _rate_limit_delay(resp: httpx.Response) -> float:
    """GitHub's documented order: retry-after, then x-ratelimit-reset, else at least a minute."""
    if (retry_after := resp.headers.get("retry-after", "")).isdigit():
        return float(retry_after)
    if resp.headers.get("x-ratelimit-remaining") == "0":
        reset = resp.headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            return max(1.0, float(reset) - time.time())
    return 60.0
