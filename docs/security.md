# Security

## Access control

- Only users with a role can run anything beyond `/start`, `/help` and `/whoami`. Unknown users
  get their Telegram ID and how to request access; GitHub is never called for them.
- In groups, the chat must be in `TELEGRAM_ALLOWED_CHAT_IDS` and the user must have a role.
  Messages from other groups are ignored without a reply.
- Every command goes through one function (`Runtime.dispatch`), which applies the chat check,
  rate limits and the role check before the handler runs. A new command cannot skip it.
- Buttons are bound to the user who caused them. Pressing someone else's button, an expired
  one, or a confirmation after losing the role does nothing.
- Admins from the environment cannot be demoted or removed from chat, so the bot cannot be
  taken over or locked by an admin added at runtime.

## GitHub permissions

Grant the minimum (table in the [README](../README.md#3-give-it-access-to-github-least-privilege)):
no Contents write, no Administration, no Secrets. The bot only manages repositories on its list,
even if the token can see more. Every action on GitHub is recorded as the token's owner or the
App, never as the Telegram user; the bot's JSON log records which Telegram user did what
(`user_id`, `command`, `cid`). This is why `approve` / `request_changes` and `/run` are admin-only
and confirmed.

Prefer a GitHub App for organizations: short-lived (1 hour) installation tokens, per-repository
installation, and actions attributed to the app.

## Input handling

- Repository names, users, refs, workflow files, numbers and workflow inputs are validated
  against strict patterns before being placed in a URL path; `owner/..`-style traversal is rejected.
  Label names are URL-encoded.
- Free text (titles, bodies, comments) has control characters stripped and length limits.
- Everything coming back from GitHub is HTML-escaped before it is sent to Telegram.
- No shell, `eval`, or subprocess is ever derived from a message.

## Secrets

- Secrets come only from the environment. `.env`, `*.pem` and databases are git- and
  docker-ignored.
- All log output passes through one formatter that replaces every configured secret value and
  anything shaped like a Telegram bot token, a GitHub token (`ghp_`, `ghs_`, `github_pat_`…) or
  a PEM private key. `httpx` request logging (which would include the bot token in the URL) is
  turned down to WARNING.
- Errors that escape the bot (for example python-telegram-bot's `InvalidToken`, whose message
  contains the token) are logged through that formatter instead of a raw traceback.
- Users see "internal error, ref uN" for unexpected failures, never a stack trace.

## Abuse limits

Per-user and per-command rate limits; GitHub calls retry within a 20 s budget; writes are never
retried when GitHub may have processed them (no duplicate issues).

## CI/CD

- `ci.yml` uses `pull_request` (not `pull_request_target`): pull requests from forks run with a
  read-only token and no secrets. `permissions: contents: read`.
- `docker.yml` only gets `packages: write` in the publishing job and uses `GITHUB_TOKEN`.
- `deploy.yml` is manual, bound to the `production` environment (add required reviewers), uses
  a dedicated SSH key restricted with `restrict`, checks the pinned host key, and deletes the key
  at the end. The deploy user is in the `docker` group, which is root-equivalent on that host:
  use a host dedicated to services you trust.
- Third-party actions are pinned by commit SHA.

## Reporting

If you find a vulnerability in your deployment of this project, rotate the affected token first
(BotFather `/revoke`, GitHub token or App key), then fix.
