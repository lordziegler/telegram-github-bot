# Configuration

All configuration comes from environment variables (or `.env`, read by `make run` and Docker
Compose). Validation happens at startup in `src/tgbot/config.py`; every problem is listed at once
and the process exits with code 2.

## Telegram

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | | From @BotFather. Must look like `<digits>:<secret>`. |
| `TELEGRAM_ADMIN_USER_IDS` | yes | | Comma-separated user IDs. Always `admin`; cannot be demoted or denied from chat. |
| `TELEGRAM_ALLOWED_USER_IDS` | no | | Users allowed from the start as `viewer`. Cannot be denied from chat; can be promoted. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | no | | Groups where the bot answers (negative IDs). Private chats always work. Membership grants nothing by itself. |
| `TELEGRAM_WEBHOOK_URL` | no | | Enables webhook mode. Must be `https://`. Its path is the path the bot listens on (port 8080). |
| `TELEGRAM_WEBHOOK_SECRET` | with webhook | | 1–256 of `A-Z a-z 0-9 _ -`. Telegram sends it in `X-Telegram-Bot-Api-Secret-Token`; other requests are rejected. |

## GitHub

Set either `GITHUB_TOKEN` or the three `GITHUB_APP_*` variables. If both are set, the App is used.

| Variable | Notes |
|---|---|
| `GITHUB_TOKEN` | Fine-grained PAT (permissions in the [README](../README.md#3-give-it-access-to-github-least-privilege)). A classic PAT also works but grants far more than needed. |
| `GITHUB_APP_ID` | App ID or client ID (used as the JWT issuer). |
| `GITHUB_APP_PRIVATE_KEY` | The PEM text (`\n` escapes accepted, for one-line `.env` files) or a path to the `.pem` file. |
| `GITHUB_APP_INSTALLATION_ID` | Numeric installation ID. Installation tokens are minted on demand and refreshed 5 minutes before their 1-hour expiry. Requires the `github-app` extra. |

## Application

| Variable | Default | Notes |
|---|---|---|
| `DEFAULT_REPOSITORIES` | | `owner/repo,owner/repo`. Fixed; more can be added with `/admin repo add`. Duplicates are ignored. |
| `DEFAULT_TIMEZONE` | `UTC` (`.env.example` sets `America/Bogota`) | IANA zone for displayed dates. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `DATABASE_URL` | in memory | `sqlite:///relative.db` or `sqlite:////absolute.db`; parent directories are created. In memory, changes made from Telegram are lost on restart (a warning is logged). Compose sets `/data/bot.db`. |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `30` | Per user, all commands. Some commands add their own limit (`/run` 3, `/issue_new` 5, `/review` 5, `/comment` 10). |
| `MAX_MESSAGE_LENGTH` | `4000` | 500–4096. Longer replies are sent as plain text, cut to this length. |
| `BOT_LOCALE` | `es` | Name of a file in `src/tgbot/locales/`. |

## Secrets

Never commit `.env`. In logs, every configured secret value and anything shaped like a Telegram
or GitHub token or a PEM private key is replaced by `[REDACTED]`. See [security.md](security.md).
