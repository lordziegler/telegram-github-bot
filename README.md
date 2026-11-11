# telegram-github-bot

A self-hosted Telegram bot to work with GitHub repositories from a chat: browse and create
issues, comment, label, assign, review pull requests, check CI, and trigger GitHub Actions
workflows. Access is limited to an allowlist of Telegram users with three roles.

Everything specific to you (tokens, user IDs, repositories) lives in environment variables;
nobody needs to edit the code to run their own instance. The bot's interface is in Spanish
and every text lives in [`src/tgbot/locales/es.json`](src/tgbot/locales/es.json).

| Role | Can |
|---|---|
| `viewer` | `/repos` `/repo` `/issues` `/issue` `/prs` `/pr` `/ci` `/runs` |
| `maintainer` | the above + `/issue_new` `/comment` `/label` `/assign` `/close` `/reopen` `/review … comment` |
| `admin` | the above + `/review … approve\|request_changes` `/run` `/admin …` |

`/start`, `/help` and `/whoami` work for everyone (unknown users see their Telegram ID so they
can ask for access). Closing, reopening, approving, requesting changes, running workflows and
`/admin deny|role|repo remove` ask for confirmation with buttons only the requester can press.

## 1. Clone

```bash
git clone https://github.com/<you>/telegram-github-bot.git
cd telegram-github-bot
cp .env.example .env
```

## 2. Create the Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token into
   `TELEGRAM_BOT_TOKEN`.
2. You need your own Telegram user ID for `TELEGRAM_ADMIN_USER_IDS`. The bot tells you: start it
   once with a placeholder (`TELEGRAM_ADMIN_USER_IDS=1`), send it `/start`, put the ID it replies
   with into `.env`, and restart.
3. To use it in a group, add the bot to the group and put the group's chat ID (a negative
   number) in `TELEGRAM_ALLOWED_CHAT_IDS`. Users still need to be allowed individually.

## 3. Give it access to GitHub (least privilege)

**Option A: fine-grained personal access token** (simplest). In GitHub → Settings → Developer
settings → Fine-grained tokens: choose the resource owner, *Only select repositories*, and grant
exactly:

| Repository permission | Access | Used by |
|---|---|---|
| Metadata | Read (mandatory) | everything |
| Issues | Read and write | `/issues` `/issue` `/issue_new` `/comment` `/close` `/reopen` `/label` `/assign` |
| Pull requests | Read and write | `/prs` `/pr` `/review` |
| Commit statuses | Read | `/ci` |
| Actions | Read and write | `/runs`, `/ci`; write only for `/run` (use Read if you won't trigger workflows) |

Put it in `GITHUB_TOKEN`. A fine-grained token covers one owner (user or organization), and the
check-runs API is not available to it, so `/ci` shows commit statuses plus Actions runs instead.

**Option B: GitHub App** (recommended for organizations; actions appear as the app, not as a
person). Create an app with the same permissions plus *Checks: Read*, install it on the
repositories, generate a private key, and set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (PEM text
or path to the `.pem`) and `GITHUB_APP_INSTALLATION_ID` (the number at the end of the
installation's settings URL). Install with the `github-app` extra (the Docker image includes it).
If both a token and an app are configured, the app is used.

Whatever the identity, **every action on GitHub is performed as that token or app**, not as
the Telegram user. That is why approving and requesting changes are admin-only.

## 4. Configure

Fill `.env`. Required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_USER_IDS`, and GitHub credentials.
Every variable is documented in [`.env.example`](.env.example) and
[docs/configuration.md](docs/configuration.md). Invalid or missing values stop the bot at startup
with a list of exactly what to fix.

## 5. Run locally

With Docker Compose (state is kept in the `bot-data` volume):

```bash
docker compose up -d --build
docker compose logs -f bot
```

Or with Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
make install
make run        # reads .env
```

The bot uses long polling by default; no open port is needed. Webhook mode is described in
[docs/deployment.md](docs/deployment.md#webhook-mode).

## 6. Deploy

Run the same Compose file on any server with Docker. The included
[`deploy.yml`](.github/workflows/deploy.yml) workflow deploys or restarts it over SSH from the
Actions tab. Setup, secrets and alternatives are in [docs/deployment.md](docs/deployment.md).
GitHub Actions is used to *deploy* the bot, not to host it: runner jobs are time-limited and
their disk is discarded, which would wipe the roles and repositories added from Telegram.

## 7. Add repositories and users

From Telegram, as an admin:

```text
/admin repo add octo-org/api          # verifies the token can reach it
/repo octo-org/api                    # select it for your commands
/admin allow 123456789                # new user, starts as viewer
/admin role 123456789 maintainer
/admin                                # list users and repositories
```

Users and repositories set in environment variables are fixed: they cannot be removed or
demoted from chat, so an admin can never be locked out.

## 8. Add a command

Create a module in `src/tgbot/commands/`; it is loaded automatically:

```python
from tgbot.bot import Ctx, command
from tgbot.domain import Role


@command("labels", role=Role.VIEWER)
async def labels(ctx: Ctx, text: str) -> str:
    items = await ctx.gh.get(f"/repos/{ctx.repo}/labels", per_page=100)
    return ", ".join(label["name"] for label in items) or "-"
```

Add `cmd.labels.desc`, `cmd.labels.usage` and `cmd.labels.example` to the locale file and a test.
Authorization, rate limiting, error messages and logging are applied for you. See
[docs/extending.md](docs/extending.md).

## Development

```bash
make install     # uv sync --locked --all-extras
make lint        # ruff check + format check
make typecheck   # mypy --strict
make test        # pytest: unit + integration (Telegram and GitHub faked, no network)
make build       # docker image
```

CI runs all of the above on Python 3.11 and 3.14 and builds the image.

## Not included (on purpose)

Deleting repositories or releases, force-pushing, editing branch protection, merging PRs, and
notifications pushed from GitHub webhooks are out of scope for this version. See
[docs/extending.md](docs/extending.md#future-extensions).

## Documentation

- [Architecture and design decisions](docs/architecture.md)
- [Configuration reference](docs/configuration.md)
- [Deployment](docs/deployment.md)
- [Security](docs/security.md)
- [Extending](docs/extending.md)

License: [MIT](LICENSE).
