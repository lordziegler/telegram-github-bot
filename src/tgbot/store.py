"""Estado en SQLite: roles concedidos desde Telegram, repos añadidos y el repo que cada
usuario tiene seleccionado.

Las variables de entorno son la base inmutable; este almacén es una capa por encima.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tgbot.config import Settings
from tgbot.domain import Role, UserError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'maintainer', 'admin'))
);
CREATE TABLE IF NOT EXISTS repos (name TEXT PRIMARY KEY COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS selection (user_id INTEGER PRIMARY KEY, repo TEXT NOT NULL);
"""


class Store:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.database_path != ":memory:":
            Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(settings.database_path, isolation_level=None)
        self.db.executescript(_SCHEMA)

    # -- acceso ----------------------------------------------------------------------------------

    def role_of(self, user_id: int) -> Role | None:
        if user_id in self.settings.admin_user_ids:
            return Role.ADMIN
        row = self.db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return Role.parse(row[0])
        if user_id in self.settings.allowed_user_ids:
            return Role.VIEWER
        return None

    def allow(self, user_id: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO users (user_id, role) VALUES (?, 'viewer')", (user_id,)
        )

    def set_role(self, user_id: int, role: Role) -> None:
        if user_id in self.settings.admin_user_ids:
            raise UserError("env_admin_locked", user_id=user_id)
        self.db.execute(
            "INSERT INTO users (user_id, role) VALUES (?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET role = excluded.role",
            (user_id, role.label),
        )

    def deny(self, user_id: int) -> None:
        if user_id in self.settings.admin_user_ids or user_id in self.settings.allowed_user_ids:
            raise UserError("env_user_locked", user_id=user_id)
        if not self.db.execute("DELETE FROM users WHERE user_id = ?", (user_id,)).rowcount:
            raise UserError("unknown_user", user_id=user_id)
        self.db.execute("DELETE FROM selection WHERE user_id = ?", (user_id,))

    def users(self) -> list[tuple[int, Role, str]]:
        """(user_id, rol, origen) de todos los que tienen acceso; origen es "env" o "db"."""
        found: dict[int, tuple[int, Role, str]] = {}
        for uid in self.settings.allowed_user_ids:
            found[uid] = (uid, Role.VIEWER, "env")
        for uid, role in self.db.execute("SELECT user_id, role FROM users"):
            found[uid] = (uid, Role.parse(role), "db")
        for uid in self.settings.admin_user_ids:
            found[uid] = (uid, Role.ADMIN, "env")
        return sorted(found.values(), key=lambda u: (-u[1], u[0]))

    # -- repositorios ----------------------------------------------------------------------------

    def repos(self) -> list[str]:
        extra = [r for (r,) in self.db.execute("SELECT name FROM repos ORDER BY name")]
        env_lower = {r.lower() for r in self.settings.repositories}
        return [*self.settings.repositories, *(r for r in extra if r.lower() not in env_lower)]

    def find_repo(self, name: str) -> str | None:
        """El repo gestionado que coincide con `name` (GitHub no distingue mayúsculas)."""
        return next((r for r in self.repos() if r.lower() == name.lower()), None)

    def add_repo(self, name: str) -> None:
        if self.find_repo(name):
            raise UserError("repo_exists", repo=name)
        self.db.execute("INSERT INTO repos (name) VALUES (?)", (name,))

    def remove_repo(self, name: str) -> None:
        if any(r.lower() == name.lower() for r in self.settings.repositories):
            raise UserError("env_repo_locked", repo=name)
        if not self.db.execute("DELETE FROM repos WHERE name = ?", (name,)).rowcount:
            raise UserError("repo_not_managed", repo=name)

    def select(self, user_id: int, repo: str) -> None:
        self.db.execute(
            "INSERT INTO selection (user_id, repo) VALUES (?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET repo = excluded.repo",
            (user_id, repo),
        )

    def selected(self, user_id: int) -> str:
        """El repo seleccionado por el usuario o, si no hay, el primero gestionado."""
        row = self.db.execute("SELECT repo FROM selection WHERE user_id = ?", (user_id,)).fetchone()
        if row and (repo := self.find_repo(row[0])):
            return repo
        repos = self.repos()
        if not repos:
            raise UserError("no_repos")
        return repos[0]
