# telegram-github-bot

Telegram bot that manages GitHub repositories. Python 3.11+, python-telegram-bot 22, httpx, SQLite.

## Commands

- `make install` · `make lint` · `make typecheck` · `make test` · `make check` (all three) · `make build`
- `make format` fixes formatting and autofixable lint.
- Tests need no network: `tests/conftest.py` fakes the Telegram Bot API (`FakeTelegram`, a
  `BaseRequest`) and GitHub (`FakeGitHub`, an `httpx.MockTransport` route table).

## Layout

See `docs/architecture.md`. Short version: `domain.py` (pure rules) · `config.py` · `log.py` ·
`store.py` (SQLite overlay on env) · `github.py` (REST client) · `bot.py` (registry + dispatch) ·
`commands/*.py` (auto-imported, `@command`) · `locales/es.json` (all UI text).

## Invariants — keep them

- Every command enters through `Runtime.dispatch`; register with `@command`, never with a raw
  python-telegram-bot handler.
- UI text lives in `locales/es.json` (Spanish). Code, comments, logs, docs and commits: English.
- Validate user values with `domain.py` parsers before they reach a GitHub URL path.
- Never log message text or secrets; logs go through `JsonFormatter`, which redacts.
- Writes to GitHub (POST/PATCH) must not be retried after the request may have been processed.
- Irreversible actions or actions done as the token's owner go through `ctx.confirm`.
- Edited messages must never run commands (`allowed_updates` + `filters.UpdateType.MESSAGE`).
- Env-configured admins/users/repos are immutable from chat (lockout protection).
- Every new command needs `cmd.<name>.desc|usage|example` locale keys (a test enforces it).
