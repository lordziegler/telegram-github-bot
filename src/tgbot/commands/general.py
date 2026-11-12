"""Bienvenida, identidad y selección de repositorio."""

from __future__ import annotations

import asyncio
import html

from tgbot.bot import Ctx, command, ordered_commands
from tgbot.domain import Role, UserError, parse_repo
from tgbot.github import GitHubError


@command("start", role=None)
async def start(ctx: Ctx, _: str) -> str:
    if ctx.role is None:
        return ctx.t("start_unknown", user_id=ctx.user_id)
    return ctx.t("start_known", name=ctx.user_name, role=ctx.role.label)


@command("help", role=None)
async def help_(ctx: Ctx, _: str) -> str:
    lines = [ctx.t("help_header")]
    for c in ordered_commands():
        if c.role is None or (ctx.role is not None and ctx.role >= c.role):
            lines.append(
                ctx.t(
                    "help_line",
                    usage=ctx.t(f"cmd.{c.name}.usage"),
                    desc=ctx.t(f"cmd.{c.name}.desc"),
                    example=ctx.t(f"cmd.{c.name}.example"),
                )
            )
    if ctx.role is None:
        lines.append(ctx.t("help_no_access", user_id=ctx.user_id))
    return "\n".join(lines)


@command("whoami", role=None)
async def whoami(ctx: Ctx, _: str) -> str:
    if ctx.role is None:
        return ctx.t("whoami_unknown", user_id=ctx.user_id, name=ctx.user_name)
    repos = ctx.store.repos()
    current = ctx.repo if repos else "-"
    return ctx.t(
        "whoami",
        user_id=ctx.user_id,
        name=ctx.user_name,
        role=ctx.role.label,
        repo=current,
        repos=", ".join(repos) or "-",
    )


@command("repos", role=Role.VIEWER)
async def repos(ctx: Ctx, _: str) -> str:
    names = ctx.store.repos()
    if not names:
        raise UserError("no_repos")
    results = await asyncio.gather(
        *(ctx.gh.get(f"/repos/{n}") for n in names), return_exceptions=True
    )
    current = ctx.repo
    lines = [ctx.t("repos_header")]
    for name, result in zip(names, results, strict=True):
        if isinstance(result, GitHubError):
            state = ctx.t("repo_inaccessible", status=result.status)
        elif isinstance(result, BaseException):
            raise result
        else:
            state = ctx.t("repo_private" if result["private"] else "repo_public")
        mark = "▶ " if name == current else "• "
        lines.append(f"{mark}<code>{html.escape(name)}</code> — {state}")
    lines.append(ctx.t("repos_footer"))
    return "\n".join(lines)


@command("repo", role=Role.VIEWER)
async def repo(ctx: Ctx, text: str) -> str:
    if text:
        managed = ctx.store.find_repo(parse_repo(text))
        if managed is None:
            raise UserError("repo_not_managed", repo=text)
        ctx.store.select(ctx.user_id, managed)
    name = ctx.repo
    r = await ctx.gh.get(f"/repos/{name}")
    return ctx.t(
        "repo_info",
        repo=r["full_name"],
        url=r["html_url"],
        description=r.get("description") or "-",
        visibility=ctx.t("repo_private" if r["private"] else "repo_public"),
        branch=r["default_branch"],
        stars=r["stargazers_count"],
        open_items=r["open_issues_count"],
        pushed=ctx.date(r.get("pushed_at")),
    )
