"""Issues (and the issue-level actions that also apply to PRs: comment, close, reopen, labels)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from tgbot.bot import Ctx, Html, Reply, command
from tgbot.domain import Role, UserError, clean_text, parse_number, parse_user

STATES = {"open", "closed", "all"}


def _names(items: list[dict[str, Any]], key: str) -> str:
    return ", ".join(str(i[key]) for i in items) or "-"


def issue_line(ctx: Ctx, item: dict[str, Any]) -> str:
    return ctx.t(
        "issue_line",
        number=item["number"],
        url=item["html_url"],
        title=item["title"][:120],
        user=item["user"]["login"],
        labels=_names(item.get("labels", []), "name"),
    )


@command("issues", role=Role.VIEWER)
async def issues(ctx: Ctx, text: str) -> Reply:
    state, params = "open", dict[str, Any]()
    labels: list[str] = []
    for token in text.split():
        key, _, value = token.partition(":")
        if token in STATES:
            state = token
        elif key == "label" and value:
            labels.append(clean_text(value, 50))
        elif key == "assignee" and value:
            params["assignee"] = value if value in {"none", "*"} else parse_user(value)
        else:
            raise UserError("usage")
    if labels:
        params["labels"] = ",".join(labels)
    repo = ctx.repo

    async def render(page: int) -> tuple[str, bool]:
        items, has_next = await ctx.gh.page(f"/repos/{repo}/issues", page, state=state, **params)
        # The issues endpoint also returns pull requests; they are listed by /prs.
        # ponytail: filtering after paging can leave short pages; use the search API if it matters.
        found = [i for i in items if "pull_request" not in i]
        lines = [ctx.t("issues_header", repo=repo, state=Html(ctx.t(f"state_{state}")), page=page)]
        lines += [issue_line(ctx, i) for i in found] or [ctx.t("empty_page")]
        return "\n".join(lines), has_next

    return await ctx.paginate(render)


@command("issue", role=Role.VIEWER)
async def issue(ctx: Ctx, text: str) -> str:
    if not text:
        raise UserError("usage")
    number = parse_number(text)
    i = await ctx.gh.get(f"/repos/{ctx.repo}/issues/{number}")
    body = (i.get("body") or "").strip()
    return ctx.t(
        "issue_detail",
        number=number,
        url=i["html_url"],
        title=i["title"],
        state=ctx.t(f"state_{i['state']}"),
        user=i["user"]["login"],
        labels=_names(i.get("labels", []), "name"),
        assignees=_names(i.get("assignees", []), "login"),
        comments=i["comments"],
        created=ctx.date(i["created_at"]),
        body=body[:1500] + ("…" if len(body) > 1500 else "") or "-",
        pr_hint=Html(ctx.t("is_pr_hint", number=number) if "pull_request" in i else ""),
    )


@command("issue_new", role=Role.MAINTAINER, per_minute=5)
async def issue_new(ctx: Ctx, text: str) -> str:
    title, _, body = text.partition("|")
    title = clean_text(title, 256)
    if not title:
        raise UserError("usage")
    i = await ctx.gh.send(
        "POST", f"/repos/{ctx.repo}/issues", {"title": title, "body": clean_text(body, 60000)}
    )
    return ctx.t("issue_created", number=i["number"], url=i["html_url"], title=i["title"])


@command("comment", role=Role.MAINTAINER, per_minute=10)
async def comment(ctx: Ctx, text: str) -> str:
    number_text, _, body = text.partition(" ")
    body = clean_text(body, 60000)
    if not number_text or not body:
        raise UserError("usage")
    number = parse_number(number_text)
    c = await ctx.gh.send("POST", f"/repos/{ctx.repo}/issues/{number}/comments", {"body": body})
    return ctx.t("comment_created", number=number, url=c["html_url"])


async def _set_state(ctx: Ctx, text: str, state: str) -> Reply:
    if not text:
        raise UserError("usage")
    number, repo = parse_number(text), ctx.repo
    i = await ctx.gh.get(f"/repos/{repo}/issues/{number}")  # show what is about to change
    if i["state"] == state:
        raise UserError(f"already_{state}", number=number)

    async def apply() -> str:
        await ctx.gh.send("PATCH", f"/repos/{repo}/issues/{number}", {"state": state})
        return ctx.t(f"done_{state}", number=number, url=i["html_url"])

    return ctx.confirm(ctx.t(f"confirm_{state}", number=number, title=i["title"], repo=repo), apply)


@command("close", role=Role.MAINTAINER)
async def close(ctx: Ctx, text: str) -> Reply:
    return await _set_state(ctx, text, "closed")


@command("reopen", role=Role.MAINTAINER)
async def reopen(ctx: Ctx, text: str) -> Reply:
    return await _set_state(ctx, text, "open")


def _split_changes(text: str, parse: Any) -> tuple[int, list[str], list[str]]:
    """`12 a,b,-c` -> (12, [a, b], [c]). A leading '-' removes."""
    number_text, _, rest = text.partition(" ")
    names = [n.strip() for n in rest.split(",") if n.strip()]
    if not number_text or not names:
        raise UserError("usage")
    add = [parse(n) for n in names if not n.startswith("-")]
    remove = [parse(n[1:]) for n in names if n.startswith("-")]
    return parse_number(number_text), add, remove


@command("label", role=Role.MAINTAINER)
async def label(ctx: Ctx, text: str) -> str:
    number, add, remove = _split_changes(text, lambda n: clean_text(n, 50))
    base = f"/repos/{ctx.repo}/issues/{number}/labels"
    current: list[dict[str, Any]] | None = None
    if add:
        current = await ctx.gh.send("POST", base, {"labels": add})
    for name in remove:
        current = await ctx.gh.send("DELETE", f"{base}/{quote(name, safe='')}")
    return ctx.t("labels_now", number=number, labels=_names(current or [], "name"))


@command("assign", role=Role.MAINTAINER)
async def assign(ctx: Ctx, text: str) -> str:
    number, add, remove = _split_changes(text, parse_user)
    base = f"/repos/{ctx.repo}/issues/{number}/assignees"
    i: dict[str, Any] = {}
    if add:
        i = await ctx.gh.send("POST", base, {"assignees": add})
    if remove:
        i = await ctx.gh.send("DELETE", base, {"assignees": remove})
    now = [a["login"] for a in i.get("assignees", [])]
    # GitHub silently ignores assignees without access to the repository.
    ignored = [u for u in add if u.lower() not in {n.lower() for n in now}]
    reply = ctx.t("assignees_now", number=number, assignees=", ".join(now) or "-")
    if ignored:
        reply += "\n" + ctx.t("assignees_ignored", users=", ".join(ignored))
    return reply
