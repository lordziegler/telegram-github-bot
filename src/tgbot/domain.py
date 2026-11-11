"""Reglas puras: roles, validación de entradas y límite de frecuencia.

Sin E/S, sin Telegram y sin GitHub.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from collections.abc import Callable, Hashable
from enum import IntEnum


class Role(IntEnum):
    VIEWER = 1
    MAINTAINER = 2
    ADMIN = 3

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, value: str) -> Role:
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise UserError("bad_role", value=value) from None


class UserError(Exception):
    """Error causado por la entrada del usuario. `key` es una clave de traducción; los kwargs,
    sus parámetros."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


# GitHub: owner de 1-39 caracteres (alfanuméricos o guion); nombre de repo con alfanuméricos
# y . _ - hasta 100, nunca "." ni "..".
_REPO = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})$")
_USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_REF = re.compile(r"^(?!.*\.\.)(?!/)(?!.*//)[A-Za-z0-9._/-]{1,200}(?<![./])$")
_WORKFLOW = re.compile(r"^(?:\d+|[A-Za-z0-9._-]{1,100}\.ya?ml)$")
_INPUT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,99}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def parse_repo(value: str) -> str:
    m = _REPO.match(value.strip())
    if not m or set(m.group(2)) == {"."}:
        raise UserError("bad_repo", value=value)
    return f"{m.group(1)}/{m.group(2)}"


def parse_number(value: str) -> int:
    v = value.strip().lstrip("#")
    if not v.isdigit() or not 0 < int(v) < 10**9:
        raise UserError("bad_number", value=value)
    return int(v)


def parse_user(value: str) -> str:
    v = value.strip().lstrip("@")
    if not _USER.match(v):
        raise UserError("bad_user", value=value)
    return v


def parse_ref(value: str) -> str:
    if not _REF.match(value):
        raise UserError("bad_ref", value=value)
    return value


def parse_ci_target(value: str) -> int | str:
    """`42` -> número de PR, `abc1234` -> SHA de un commit."""
    v = value.strip().lstrip("#")
    # ponytail: hasta 6 dígitos es un número de PR; 7 o más dígitos se tratan como SHA.
    if v.isdigit() and len(v) <= 6:
        return parse_number(v)
    if _SHA.match(v):
        return v.lower()
    raise UserError("bad_ci_target", value=value)


def parse_workflow(value: str) -> str:
    if not _WORKFLOW.match(value):
        raise UserError("bad_workflow", value=value)
    return value


def parse_inputs(tokens: list[str]) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for token in tokens:
        key, sep, val = token.partition("=")
        if not sep or not _INPUT_KEY.match(key):
            raise UserError("bad_input", value=token)
        inputs[key] = clean_text(val, 1000)
    if len(inputs) > 25:  # máximo documentado por GitHub para workflow_dispatch
        raise UserError("too_many_inputs")
    return inputs


def clean_text(value: str, limit: int) -> str:
    """Quita caracteres de control (conserva \\t y \\n) y aplica un límite de longitud."""
    text = _CONTROL.sub("", value).strip()
    if len(text) > limit:
        raise UserError("too_long", limit=limit)
    return text


class RateLimiter:
    """Limitador de ventana deslizante, en memoria."""

    # ponytail: estado por proceso; basta porque el long polling de Telegram admite una sola
    # instancia de todos modos.
    def __init__(self, clock: Callable[[], float] = time.monotonic, window: float = 60.0) -> None:
        self._clock = clock
        self._window = window
        self._hits: defaultdict[Hashable, deque[float]] = defaultdict(deque)

    def allow(self, key: Hashable, limit: int) -> bool:
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def prune(self) -> None:
        now = self._clock()
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self._window]:
            del self._hits[key]
