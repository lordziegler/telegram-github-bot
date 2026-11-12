"""/secret: secrets de GitHub Actions del repositorio activo. Solo admin.

El valor viaja por Telegram, así que: solo en chat privado, el mensaje se borra en cuanto llega
y se cifra antes de pedir confirmación (en memoria solo queda el texto cifrado).
"""

from __future__ import annotations

import base64
import logging

from nacl.public import PublicKey, SealedBox
from telegram.error import TelegramError

from tgbot.bot import Ctx, Reply, command
from tgbot.domain import Role, UserError, parse_secret_name
from tgbot.github import GitHubError

log = logging.getLogger(__name__)


def encrypt(public_key_b64: str, value: str) -> str:
    """Sealed box de libsodium con la clave pública del repo, como pide GitHub."""
    box = SealedBox(PublicKey(base64.b64decode(public_key_b64)))
    return base64.b64encode(box.encrypt(value.encode())).decode()


async def _delete_message(ctx: Ctx) -> bool:
    try:
        return bool(ctx.message and await ctx.message.delete())
    except TelegramError:
        log.warning("no se pudo borrar el mensaje con el secret")
        return False


@command("secret", role=Role.ADMIN, per_minute=5)
async def secret(ctx: Ctx, text: str) -> Reply | str:
    action, *rest = text.split(maxsplit=2) or ["list"]
    action = action.lower()
    # Con tres palabras puede haber un valor, aunque la acción esté mal escrita: borrar primero.
    deleted = len(rest) == 2 and await _delete_message(ctx)
    repo = ctx.repo
    if action == "list" and not rest:
        data = await ctx.gh.get(f"/repos/{repo}/actions/secrets", per_page=100)
        lines = [ctx.t("secrets_header", repo=repo, count=data["total_count"])]
        lines += [
            ctx.t("secret_line", name=s["name"], updated=ctx.date(s["updated_at"]))
            for s in data["secrets"]
        ] or [ctx.t("secrets_none")]
        return "\n".join(lines)

    if action == "set" and len(rest) == 2:
        if ctx.message is None or ctx.message.chat.type != "private":
            raise UserError("secret_private_only", deleted="✅" if deleted else "❌")
        name, value = parse_secret_name(rest[0]), rest[1].strip()
        if not value or len(value.encode()) > 48 * 1024:  # límite documentado: 48 KB
            raise UserError("secret_bad_value")
        key = await ctx.gh.get(f"/repos/{repo}/actions/secrets/public-key")
        payload = {"encrypted_value": encrypt(key["key"], value), "key_id": key["key_id"]}
        del value  # a partir de aquí solo existe cifrado
        try:
            await ctx.gh.get(f"/repos/{repo}/actions/secrets/{name}")
            verb = "secret_verb_replace"
        except GitHubError as exc:
            if exc.status != 404:
                raise
            verb = "secret_verb_create"

        async def put() -> str:
            await ctx.gh.send("PUT", f"/repos/{repo}/actions/secrets/{name}", payload)
            return ctx.t("secret_saved", name=name, repo=repo)

        prompt = ctx.t("confirm_secret_set", verb=ctx.t(verb), name=name, repo=repo)
        note = ctx.t("secret_deleted_note" if deleted else "secret_not_deleted_note")
        return ctx.confirm(prompt + "\n" + note, put)

    if action == "delete" and len(rest) == 1:
        name = parse_secret_name(rest[0])

        async def delete() -> str:
            await ctx.gh.send("DELETE", f"/repos/{repo}/actions/secrets/{name}")
            return ctx.t("secret_removed", name=name, repo=repo)

        return ctx.confirm(ctx.t("confirm_secret_delete", name=name, repo=repo), delete)

    raise UserError("usage")
