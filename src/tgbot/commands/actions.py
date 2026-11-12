"""GitHub Actions: lanzar un workflow y revisar sus ejecuciones."""

from __future__ import annotations

import html
import re
import shlex
from collections.abc import Awaitable, Callable
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


@command("workflows", role=Role.VIEWER)
async def workflows(ctx: Ctx, _: str) -> str:
    repo = ctx.repo
    data = await ctx.gh.get(f"/repos/{repo}/actions/workflows", per_page=100)
    lines = [ctx.t("workflows_header", repo=repo)]
    lines += [
        ctx.t(
            "workflow_line",
            name=w["name"],
            file=w["path"].rsplit("/", 1)[-1],
            state=w["state"],
            url=w["html_url"],
        )
        for w in data["workflows"]
    ] or [ctx.t("workflows_none")]
    return "\n".join(lines)


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


def _details(ctx: Ctx, repo: str, listed: dict[str, Any]) -> Callable[[], Awaitable[Reply]]:
    async def show() -> Reply:
        # La lista pudo quedarse vieja: el estado (p. ej. "waiting") se pide de nuevo.
        run_ = await ctx.gh.get(f"/repos/{repo}/actions/runs/{listed['id']}")
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
        buttons: list[tuple[str, str]] = []
        if ctx.role == Role.ADMIN:
            buttons, waiting_info = await _run_actions(ctx, repo, run_)
            lines += [ctx.t("deploy_waiting_info")] if waiting_info else []
        return Reply("\n".join(lines), [buttons] if buttons else [])

    return show


async def _run_actions(
    ctx: Ctx, repo: str, run_: dict[str, Any]
) -> tuple[list[tuple[str, str]], bool]:
    """Botones de CI/CD para admins: cancelar, reintentar, aprobar o rechazar un despliegue.
    Cada botón es de un solo uso y es en sí la confirmación de esa acción concreta.
    Devuelve también si hay un despliegue esperando que este token no puede aprobar."""
    base = f"/repos/{repo}/actions/runs/{run_['id']}"
    number = run_["run_number"]
    review: list[tuple[str, str]] = []  # aprobar y rechazar se excluyen entre sí

    def post(
        path: str, done: str, body: dict[str, Any] | None = None
    ) -> Callable[[], Awaitable[str]]:
        async def act() -> str:
            await ctx.gh.send("POST", base + path, body)
            for _, data in review:  # tras aprobar o rechazar, el otro botón deja de valer
                ctx.rt.interactions.pop(data.partition(":")[0], None)
            return ctx.t(done, number=number)

        return act

    buttons = []
    waiting_info = False
    if run_["status"] == "waiting":  # un environment con revisores obligatorios espera
        pending = await ctx.gh.get(base + "/pending_deployments")
        ids = [p["environment"]["id"] for p in pending if p["current_user_can_approve"]]
        waiting_info = bool(pending) and not ids
        if ids:
            for state, label, done in (
                ("approved", "btn_approve_deploy", "deploy_approved"),
                ("rejected", "btn_reject_deploy", "deploy_rejected"),
            ):
                body = {"environment_ids": ids, "state": state, "comment": ctx.t("deploy_comment")}
                act = post("/pending_deployments", done, body)
                review.append(ctx.button(ctx.t(label), act, Role.ADMIN, once=True))
            buttons += review
    if run_["status"] != "completed":
        act = post("/cancel", "run_cancelled")
        buttons.append(ctx.button(ctx.t("btn_cancel_run"), act, Role.ADMIN, once=True))
    elif run_["conclusion"] in {"failure", "cancelled", "timed_out"}:
        act = post("/rerun-failed-jobs", "run_rerun")
        buttons.append(ctx.button(ctx.t("btn_rerun"), act, Role.ADMIN, once=True))
    return buttons, waiting_info
