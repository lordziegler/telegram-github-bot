"""GitHub Actions: lanzar un workflow y revisar sus ejecuciones."""

from __future__ import annotations

import html
import re
import shlex
from typing import Any

from tgbot.bot import Ctx, Reply, command
from tgbot.commands.pulls import icon, link
from tgbot.domain import Role, UserError, parse_inputs, parse_ref, parse_workflow

_LOG_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")


@command("run", role=Role.ADMIN, per_minute=3)
async def run(ctx: Ctx, text: str) -> Reply:
    try:
        tokens = shlex.split(text)
    except ValueError:
        raise UserError("usage") from None
    if not tokens:
        raise UserError("usage")
    name, _, ref = tokens[0].partition("@")
    workflow, inputs, repo = parse_workflow(name), parse_inputs(tokens[1:]), ctx.repo
    wf = await ctx.gh.get(f"/repos/{repo}/actions/workflows/{workflow}")
    if wf["state"] != "active":
        raise UserError("workflow_inactive", workflow=wf["name"], state=wf["state"])
    ref = parse_ref(ref) if ref else (await ctx.gh.get(f"/repos/{repo}"))["default_branch"]

    async def dispatch() -> str:
        result = await ctx.gh.send(
            "POST",
            f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
            {"ref": ref, "inputs": inputs},
        )
        # GitHub actual responde 200 con la ejecución; servidores antiguos, 204 sin cuerpo.
        url = (result or {}).get("html_url")
        return ctx.t(
            "run_started" if url else "run_started_nourl",
            workflow=wf["name"],
            url=url or "",
            name=workflow,
        )

    shown = ", ".join(f"{k}={v}" for k, v in inputs.items()) or "-"
    prompt = ctx.t(
        "confirm_run", workflow=wf["name"], path=wf["path"], ref=ref, inputs=shown, repo=repo
    )
    return ctx.confirm(prompt, dispatch)


@command("runs", role=Role.VIEWER)
async def runs(ctx: Ctx, text: str) -> Reply:
    if not text:
        raise UserError("usage")
    workflow, repo = parse_workflow(text.strip()), ctx.repo

    async def render(page: int) -> tuple[Reply, bool]:
        data, has_next = await ctx.gh.page(
            f"/repos/{repo}/actions/workflows/{workflow}/runs", page, per_page=8
        )
        items: list[dict[str, Any]] = data.get("workflow_runs", [])
        lines = [ctx.t("runs_header", workflow=workflow, repo=repo, page=page)]
        buttons: list[tuple[str, str]] = []
        for r in items:
            lines.append(
                ctx.t(
                    "run_line",
                    icon=icon(r["status"], r["conclusion"]),
                    number=r["run_number"],
                    url=r["html_url"],
                    branch=r.get("head_branch") or "-",
                    event=r["event"],
                    created=ctx.date(r["created_at"]),
                )
            )
            buttons.append(ctx.button(f"#{r['run_number']}", _details(ctx, repo, r)))
        if not items:
            lines.append(ctx.t("empty_page"))
        rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
        return Reply("\n".join(lines), rows), has_next

    return await ctx.paginate(render)


def _details(ctx: Ctx, repo: str, run_: dict[str, Any]) -> Any:
    async def show() -> str:
        data = await ctx.gh.get(f"/repos/{repo}/actions/runs/{run_['id']}/jobs", per_page=30)
        jobs: list[dict[str, Any]] = data.get("jobs", [])
        lines = [
            ctx.t(
                "run_detail_header",
                name=run_["name"],
                number=run_["run_number"],
                url=run_["html_url"],
                status=run_["conclusion"] or run_["status"],
            )
        ]
        failed_job = None
        for job in jobs:
            lines.append(
                f"{icon(job['status'], job['conclusion'])} "
                f"{link(job['name'], job.get('html_url') or '')}"
            )
            if job["conclusion"] == "failure":
                failed_job = failed_job or job
                lines += [
                    f"   └ ❌ {html.escape(step['name'])}"
                    for step in job.get("steps") or []
                    if step.get("conclusion") == "failure"
                ]
        if failed_job:
            tail = await ctx.gh.log_tail(f"/repos/{repo}/actions/jobs/{failed_job['id']}/logs", 20)
            text = "\n".join(_LOG_TIMESTAMP.sub("", line) for line in tail)[-1500:]
            lines.append(ctx.t("run_log_tail", job=failed_job["name"]))
            lines.append(f"<pre>{html.escape(text)}</pre>")
        return "\n".join(lines)

    return show
