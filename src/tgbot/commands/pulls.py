"""Pull requests, reviews y estado de la CI."""

from __future__ import annotations

import html
from typing import Any

from tgbot.bot import Ctx, Html, Reply, command
from tgbot.domain import Role, UserError, clean_text, parse_ci_target, parse_number
from tgbot.github import GitHubError

STATES = {"open", "closed", "all"}
REVIEW_EVENTS = {"approve": "APPROVE", "request_changes": "REQUEST_CHANGES", "comment": "COMMENT"}


def icon(status: str | None, conclusion: str | None) -> str:
    """Check runs / ejecuciones de workflow / estados de commit -> un emoji."""
    if status not in (None, "completed"):
        return "⏳"
    return {
        "success": "✅",
        "skipped": "⚪",
        "neutral": "⚪",
        "pending": "⏳",
    }.get(conclusion or "", "❌")


@command("prs", role=Role.VIEWER)
async def prs(ctx: Ctx, text: str) -> Reply:
    state = text.strip() or "open"
    if state not in STATES:
        raise UserError("usage")
    repo = ctx.repo

    async def render(page: int) -> tuple[str, bool]:
        items, has_next = await ctx.gh.page(f"/repos/{repo}/pulls", page, state=state)
        lines = [ctx.t("prs_header", repo=repo, state=Html(ctx.t(f"state_{state}")), page=page)]
        lines += [
            ctx.t(
                "pr_line",
                number=p["number"],
                url=p["html_url"],
                title=p["title"][:120],
                user=p["user"]["login"],
                head=p["head"]["ref"],
                base=p["base"]["ref"],
                draft=Html(ctx.t("draft") if p.get("draft") else ""),
            )
            for p in items
        ] or [ctx.t("empty_page")]
        return "\n".join(lines), has_next

    return await ctx.paginate(render)


@command("pr", role=Role.VIEWER)
async def pr(ctx: Ctx, text: str) -> str:
    if not text:
        raise UserError("usage")
    number = parse_number(text)
    p = await ctx.gh.get(f"/repos/{ctx.repo}/pulls/{number}")
    state = "merged" if p.get("merged") else p["state"]
    body = (p.get("body") or "").strip()
    return ctx.t(
        "pr_detail",
        number=number,
        url=p["html_url"],
        title=p["title"],
        state=Html(ctx.t(f"state_{state}")),
        draft=Html(ctx.t("draft") if p.get("draft") else ""),
        user=p["user"]["login"],
        head=p["head"]["ref"],
        base=p["base"]["ref"],
        mergeable=p.get("mergeable_state") or "-",
        commits=p.get("commits", "-"),
        additions=p.get("additions", "-"),
        deletions=p.get("deletions", "-"),
        files=p.get("changed_files", "-"),
        created=ctx.date(p["created_at"]),
        body=body[:1200] + ("…" if len(body) > 1200 else "") or "-",
    )


@command("review", role=Role.MAINTAINER, per_minute=5)
async def review(ctx: Ctx, text: str) -> Reply | str:
    parts = text.split(maxsplit=2)
    if len(parts) < 2 or parts[1] not in REVIEW_EVENTS:
        raise UserError("usage")
    number, event = parse_number(parts[0]), REVIEW_EVENTS[parts[1]]
    body = clean_text(parts[2] if len(parts) == 3 else "", 60000)
    if event != "APPROVE" and not body:
        raise UserError("review_needs_text")
    repo = ctx.repo

    async def submit() -> str:
        payload = {"event": event, **({"body": body} if body else {})}
        r = await ctx.gh.send("POST", f"/repos/{repo}/pulls/{number}/reviews", payload)
        return ctx.t("review_done", number=number, url=r["html_url"])

    if event == "COMMENT":
        return await submit()
    # Aprobar o bloquear se hace a nombre del dueño del token: solo admin y con confirmación.
    ctx.require(Role.ADMIN)
    p = await ctx.gh.get(f"/repos/{repo}/pulls/{number}")
    prompt = ctx.t(f"confirm_{parts[1]}", number=number, title=p["title"], repo=repo)
    return ctx.confirm(prompt, submit, Role.ADMIN)


@command("ci", role=Role.VIEWER)
async def ci(ctx: Ctx, text: str) -> str:
    if not text:
        raise UserError("usage")
    target, repo = parse_ci_target(text), ctx.repo
    if isinstance(target, int):
        p = await ctx.gh.get(f"/repos/{repo}/pulls/{target}")
        sha, what = p["head"]["sha"], f"PR #{target}"
    else:
        sha, what = target, target[:7]
    combined = await ctx.gh.get(f"/repos/{repo}/commits/{sha}/status")
    rows: list[tuple[str, str, str]] = [
        (icon(None, s["state"]), s["context"], s.get("target_url") or "")
        for s in combined.get("statuses", [])
    ]
    try:
        checks = await ctx.gh.get(f"/repos/{repo}/commits/{sha}/check-runs", per_page=50)
        items: list[dict[str, Any]] = checks.get("check_runs", [])
    except GitHubError as exc:
        # Los PAT fine-grained no leen check runs: se usan las ejecuciones de Actions del commit.
        if exc.status != 403:
            raise
        runs = await ctx.gh.get(f"/repos/{repo}/actions/runs", head_sha=sha, per_page=30)
        items = runs.get("workflow_runs", [])
    rows += [
        (icon(c["status"], c["conclusion"]), c["name"], c.get("html_url") or "") for c in items
    ]
    if not rows:
        return ctx.t("ci_none", what=what, sha=sha[:7])
    order = {"❌": 0, "⏳": 1, "✅": 2, "⚪": 3}
    rows.sort(key=lambda r: order[r[0]])
    count = {k: sum(1 for r in rows if r[0] == k) for k in order}
    lines = [
        ctx.t(
            "ci_header",
            what=what,
            sha=sha[:7],
            failed=count["❌"],
            pending=count["⏳"],
            ok=count["✅"],
            skipped=count["⚪"],
        )
    ]
    lines += [f"{mark} {link(name[:80], url)}" for mark, name, url in rows[:20]]
    if len(rows) > 20:
        lines.append(ctx.t("ci_more", count=len(rows) - 20))
    return "\n".join(lines)


def link(text: str, url: str) -> str:
    if not url.startswith("https://"):
        return html.escape(text)
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'
