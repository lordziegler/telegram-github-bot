"""/admin: usuarios, roles y repositorios gestionados. Los cambios se guardan en SQLite."""

from __future__ import annotations

from tgbot.bot import Ctx, Reply, command
from tgbot.domain import Role, UserError, parse_repo


def _telegram_id(value: str) -> int:
    if not value.isdigit() or len(value) > 15:
        raise UserError("bad_user_id", value=value)
    return int(value)


@command("admin", role=Role.ADMIN)
async def admin(ctx: Ctx, text: str) -> Reply | str:
    args = text.split()
    match args:
        case []:
            lines = [ctx.t("admin_users_header")]
            lines += [
                ctx.t("admin_user_line", user_id=uid, role=role.label, source=source)
                for uid, role, source in ctx.store.users()
            ]
            lines.append(ctx.t("admin_repos", repos=", ".join(ctx.store.repos()) or "-"))
            lines.append(ctx.t("cmd.admin.usage"))
            return "\n".join(lines)
        case ["allow", uid]:
            user_id = _telegram_id(uid)
            if ctx.store.role_of(user_id) is not None:
                raise UserError("already_allowed", user_id=user_id)
            ctx.store.allow(user_id)
            return ctx.t("allowed", user_id=user_id)
        case ["deny", uid]:
            user_id = _telegram_id(uid)
            if user_id == ctx.user_id:
                raise UserError("cannot_deny_self")

            async def deny() -> str:
                ctx.store.deny(user_id)
                return ctx.t("denied", user_id=user_id)

            return ctx.confirm(ctx.t("confirm_deny", user_id=user_id), deny)
        case ["role", uid, role_name]:
            user_id, role = _telegram_id(uid), Role.parse(role_name)

            async def set_role() -> str:
                ctx.store.set_role(user_id, role)
                return ctx.t("role_set", user_id=user_id, role=role.label)

            return ctx.confirm(ctx.t("confirm_role", user_id=user_id, role=role.label), set_role)
        case ["repo", "add", name]:
            repo = parse_repo(name)
            info = await ctx.gh.get(f"/repos/{repo}")  # comprueba que el token llega a él
            ctx.store.add_repo(info["full_name"])
            return ctx.t("repo_added", repo=info["full_name"])
        case ["repo", "remove", name]:
            repo = parse_repo(name)
            managed = ctx.store.find_repo(repo)
            if managed is None:
                raise UserError("repo_not_managed", repo=repo)

            async def remove() -> str:
                ctx.store.remove_repo(managed)
                return ctx.t("repo_removed", repo=managed)

            return ctx.confirm(ctx.t("confirm_repo_remove", repo=managed), remove)
        case _:
            raise UserError("usage")
