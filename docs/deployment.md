# Deployment

The bot is one long-running process with a small SQLite file. Anything that runs a container
(or Python 3.11+) and keeps a volume works. Only one instance may run per bot token: Telegram
rejects a second long-polling consumer.

## Docker Compose on a server (recommended)

```bash
sudo mkdir -p /opt/telegram-github-bot && sudo chown "$USER" /opt/telegram-github-bot
git clone https://github.com/<you>/telegram-github-bot.git /opt/telegram-github-bot
cd /opt/telegram-github-bot
cp .env.example .env && chmod 600 .env   # fill it in
docker compose up -d --build
docker compose ps                        # STATUS shows (healthy) after about a minute
```

The container runs as a non-root user with a read-only filesystem, no Linux capabilities, and
`/data` as its only writable volume. `restart: unless-stopped` brings it back after crashes and
reboots. On `docker compose stop` it receives SIGTERM and shuts down cleanly.

**Health.** The process touches a heartbeat file every 30 s; `python -m tgbot healthcheck`
(the image's `HEALTHCHECK`) fails if it is older than 90 s. It proves the event loop is alive,
not that Telegram is reachable; watch the logs for `telegram.ext` errors for that.

**Logs** are JSON lines on stdout: `docker compose logs -f bot`. Each line has `cid` (the update
that caused it), and command lines have `user_id`, `chat_id` and `command`. Message text is never
logged.

## Deploying from GitHub Actions

`.github/workflows/deploy.yml` is started by hand (Actions → Deploy → Run workflow), chooses a
branch or tag, and either **deploys** (checks out exactly that commit on the server and rebuilds,
waiting until the container is healthy) or **restarts** the container.

One-time setup:

1. On the server, create a dedicated user in the `docker` group, clone the repository at the
   path you will deploy from, and create `.env` there. The bot's secrets stay on the server.
2. Create an SSH key pair for the workflow (`ssh-keygen -t ed25519 -f deploy_key -N ""`) and add
   the public key to that user's `~/.ssh/authorized_keys`, prefixed with `restrict ` to disable
   forwarding and terminals.
3. Get the server's host key, verifying the fingerprint out of band:
   `ssh-keyscan -t ed25519 your.server`.
4. In the repository settings, create an **environment** named `production`, add required
   reviewers, and limit it to the branches you deploy from. Then add these secrets to it:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | server hostname or IP |
| `DEPLOY_USER` | the dedicated user |
| `DEPLOY_SSH_KEY` | contents of `deploy_key` (private) |
| `DEPLOY_KNOWN_HOSTS` | the `ssh-keyscan` line |

Optionally set the repository **variable** `DEPLOY_PATH` (default `/opt/telegram-github-bot`).

The workflow's `GITHUB_TOKEN` is read-only; strict host-key checking is on, and the key file is
deleted at the end of the job.

### Why not run the bot inside GitHub Actions?

A GitHub-hosted runner job is time-limited (6 hours) and its disk is discarded afterwards, so the
bot would restart on a schedule and lose the roles and repositories added from Telegram. Actions
deploys the bot; a server runs it.

## Optional: published image

`.github/workflows/docker.yml` builds and pushes `ghcr.io/<owner>/<repo>` when you push a `v*` tag
(also tagged `latest`) or run it by hand. It only uses `GITHUB_TOKEN` with `packages: write`. To
run that image instead of building on the server, replace `build: .` with
`image: ghcr.io/<owner>/<repo>:<tag>` in `docker-compose.yml`.

## Without Docker

```bash
uv sync --locked --no-dev --all-extras
uv run --env-file .env python -m tgbot
```

As a systemd service, run `/path/to/repo/.venv/bin/python -m tgbot` with
`EnvironmentFile=/path/to/repo/.env`, `Restart=on-failure`, and a `DATABASE_URL` pointing to a
writable path.

## Webhook mode

Long polling needs no inbound port and is the default. For webhooks:

1. Set `TELEGRAM_WEBHOOK_URL=https://bot.example.com/telegram-<random>` and
   `TELEGRAM_WEBHOOK_SECRET` (`openssl rand -hex 32`). Telegram only delivers to ports 443, 80,
   88 and 8443 over HTTPS.
2. Uncomment the `ports` line in `docker-compose.yml` (it binds to `127.0.0.1:8080`) and put a TLS
   reverse proxy in front, e.g. Caddy:

   ```text
   bot.example.com {
       reverse_proxy 127.0.0.1:8080
   }
   ```

The bot registers the webhook itself at startup. Requests without the secret header are
rejected by python-telegram-bot.
