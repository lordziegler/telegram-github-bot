# Architecture

The simplest layering that keeps Telegram, GitHub and the rules apart. One module per concern,
no frameworks, no dependency-injection container: objects are built once in `main.py` and
passed down.

```text
src/tgbot/
├── domain.py        rules: Role, input parsers/validators, UserError, RateLimiter   (no I/O)
├── config.py        environment → validated Settings; every problem reported at once
├── log.py           JSON logs, correlation ID, secret redaction at the single output point
├── store.py         SQLite overlay: roles granted from chat, extra repos, selected repo
├── github.py        GitHub REST client: PAT or App auth, retries, error mapping, paging
├── bot.py           Telegram adapter: command registry, dispatch, confirmations, pagination
├── commands/        one module per area; every @command is auto-registered
│   ├── general.py   /start /help /whoami /repos /repo
│   ├── issues.py    /issues /issue /issue_new /comment /close /reopen /label /assign
│   ├── pulls.py     /prs /pr /review /ci
│   ├── actions.py   /run /runs
│   └── admin.py     /admin
├── locales/es.json  every user-facing string
└── main.py          wiring, polling or webhook, heartbeat, healthcheck
```

How this maps to the layers usually asked for:

| Layer | Here |
|---|---|
| domain | `domain.py` |
| application (orchestration) | `bot.Runtime.dispatch` + the command functions |
| adapters/telegram | `bot.py`, `commands/`, `locales/` |
| adapters/github | `github.py` |
| infrastructure/config, logging, persistence | `config.py`, `log.py`, `store.py` |

## Request flow

```text
Telegram update
  → CommandHandler (MESSAGE updates only)          python-telegram-bot
  → Runtime.dispatch(cmd)                           bot.py — the only entry point
      1. chat allowed? (private, or in TELEGRAM_ALLOWED_CHAT_IDS) — otherwise ignore silently
      2. correlation ID = "u<update_id>" in a ContextVar, added to every log line
      3. rate limit: per user (RATE_LIMIT_REQUESTS_PER_MINUTE) and per command (per_minute=)
      4. role check against the command's minimum role
      5. handler(ctx, text) → Reply (text + optional buttons)
      6. any exception → actionable message (UserError / GitHubError) or "internal error, ref uN"
  → reply_text (HTML); over MAX_MESSAGE_LENGTH it degrades to plain text instead of cutting tags
```

Buttons (confirmations, pagination, run details) carry only a random token in `callback_data`
(Telegram allows 64 bytes). The action lives in memory, bound to the user who created it, the
minimum role, and an expiry (5 minutes for confirmations, 30 for lists). On every press the bot
checks presser == owner and re-reads the presser's role.

## Decisions taken where the brief was ambiguous

- **Environment is the baseline, SQLite is an overlay.** Admins in `TELEGRAM_ADMIN_USER_IDS`, users
  in `TELEGRAM_ALLOWED_USER_IDS` and repos in `DEFAULT_REPOSITORIES` cannot be removed or demoted
  from chat; this makes lockout impossible. Everything added with `/admin` goes to SQLite.
- **Users from `TELEGRAM_ALLOWED_USER_IDS` start as `viewer`.** Least privilege; admins promote.
- **An allowed group does not grant access to its members.** The chat must be allowed *and* the
  user must have a role.
- **Role matrix.** `close`/`reopen` are maintainer actions with confirmation. `review approve` and
  `request_changes` are admin-only because GitHub records them as the token's owner and they can
  satisfy branch protection. `/run` is admin-only: it executes code with the repository's secrets.
- **`/label` and `/assign` also remove** (`-name`), instead of adding two more commands.
- **Run logs** are shown from a button in `/runs`: jobs, failed steps and the last 20 log lines of
  the first failed job, streamed so a huge log never sits in memory.
- **`/ci`** combines commit statuses with check runs; fine-grained PATs cannot read check runs, so
  on 403 it falls back to Actions runs for the commit. A number of up to 6 digits is a PR; 7–40
  hex characters are a SHA.
- **Retries.** Reads retry on network errors and 5xx with exponential backoff; writes (POST/PATCH)
  retry only when GitHub certainly did not process them (connection failed, or rate limited),
  so a timeout can never create a duplicate issue. Total wait is capped at 20 s because a person
  is waiting; GitHub's `retry-after` / `x-ratelimit-reset` are honoured within that budget.
- **Edited messages are ignored** (`allowed_updates` excludes them and handlers filter on MESSAGE):
  editing `/issue_new …` must not create a second issue.
- **`GITHUB_WEBHOOK_SECRET` was dropped** from the configuration: nothing in this version receives
  GitHub webhooks. `BOT_LOCALE` was added so a translation is a new JSON file.
- **One instance.** Telegram allows a single long-polling consumer per token, so in-memory state
  (rate limiter, pending buttons) is enough. Pending buttons are lost on restart; users repeat
  the command.

## Simplifications with a known ceiling

Marked in the code with `ponytail:` comments.

- No `Protocol` interfaces: GitHub has one implementation and tests swap its HTTP transport
  (`httpx.MockTransport`) instead. Add an interface when a second provider (GitLab, Gitea) appears.
- No entity classes for issues/PRs: GitHub's JSON is rendered directly. Add them when a second
  provider needs a common shape.
- `/issues` filters pull requests out after paging, so a page can be short. Use the search API if
  that matters.
- The rate limiter and pending buttons are per process (see "One instance").
