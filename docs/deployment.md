# Despliegue

El bot es un único proceso de larga duración con un pequeño archivo SQLite. Sirve cualquier sitio
que ejecute un contenedor (o Python 3.11+) y conserve un volumen. Solo puede correr una instancia
por token de bot: Telegram rechaza un segundo consumidor de long polling.

## Docker Compose en un servidor (recomendado)

```bash
sudo mkdir -p /opt/telegram-github-bot && sudo chown "$USER" /opt/telegram-github-bot
git clone https://github.com/<tu-usuario>/telegram-github-bot.git /opt/telegram-github-bot
cd /opt/telegram-github-bot
cp .env.example .env && chmod 600 .env   # rellénalo
docker compose up -d --build
docker compose ps                        # STATUS muestra (healthy) al cabo de un minuto
```

El contenedor corre con un usuario sin privilegios, sistema de archivos de solo lectura, sin
capacidades de Linux y con `/data` como único volumen escribible. `restart: unless-stopped` lo
levanta de nuevo tras un fallo o un reinicio del servidor. Con `docker compose stop` recibe
SIGTERM y se apaga limpiamente.

**Salud.** El proceso toca un archivo de heartbeat cada 30 s; `python -m tgbot healthcheck` (el
`HEALTHCHECK` de la imagen) falla si tiene más de 90 s. Demuestra que el bucle de eventos está
vivo, no que Telegram responda; para eso, vigila en los logs los errores de `telegram.ext`.

**Logs**: líneas JSON por stdout (`docker compose logs -f bot`). Cada línea tiene `cid` (el update
que la causó), y las de comandos llevan `user_id`, `chat_id` y `command`. El texto de los mensajes
nunca se registra.

## Desplegar desde GitHub Actions

`.github/workflows/deploy.yml` se lanza a mano (Actions → Desplegar → Run workflow), con una rama
o tag elegido, y hace **deploy** (pone en el servidor exactamente ese commit y reconstruye,
esperando a que el contenedor esté sano) o **restart** (reinicia el contenedor).

Configuración, una sola vez:

1. En el servidor, crea un usuario dedicado del grupo `docker`, clona el repositorio en la ruta
   desde la que vas a desplegar y crea allí el `.env`. Los secretos del bot se quedan en el
   servidor.
2. Crea un par de claves SSH para el workflow (`ssh-keygen -t ed25519 -f deploy_key -N ""`) y añade
   la clave pública al `~/.ssh/authorized_keys` de ese usuario, precedida de `restrict ` para
   desactivar reenvíos y terminales.
3. Obtén la clave del host verificando la huella por otro canal: `ssh-keyscan -t ed25519 tu.servidor`.
4. En los ajustes del repositorio, crea un **environment** llamado `production`, añade revisores
   obligatorios y limítalo a las ramas desde las que despliegas. Después añádele estos secretos:

| Secreto | Valor |
|---|---|
| `DEPLOY_HOST` | nombre o IP del servidor |
| `DEPLOY_USER` | el usuario dedicado |
| `DEPLOY_SSH_KEY` | el contenido de `deploy_key` (la privada) |
| `DEPLOY_KNOWN_HOSTS` | la línea de `ssh-keyscan` |

Opcionalmente, define la **variable** de repositorio `DEPLOY_PATH` (por defecto
`/opt/telegram-github-bot`).

### CD: desplegar solo tras cada CI verde

Define la variable de repositorio `AUTO_DEPLOY` con el valor `true`. Desde entonces, cada push a
`main` cuya CI termine bien lanza `deploy.yml` con exactamente ese commit. Si `production` tiene
revisores obligatorios, la ejecución queda esperando: apruébala en GitHub o desde Telegram con
`/runs deploy.yml` → pulsa el número → «✅ Aprobar despliegue» (hace falta que el dueño del token
sea revisor del environment y el permiso *Deployments: Read and write*). Los PR de forks nunca
disparan el despliegue.

El `GITHUB_TOKEN` del workflow es de solo lectura; la comprobación estricta de la clave del host
está activada y el archivo de la clave se borra al terminar el job.

### ¿Por qué no ejecutar el bot dentro de GitHub Actions?

Un job en un runner alojado por GitHub tiene tiempo limitado (6 horas) y su disco se descarta al
terminar, así que el bot se reiniciaría periódicamente y perdería los roles y repositorios
añadidos desde Telegram. Actions despliega el bot; un servidor lo ejecuta.

## Opcional: imagen publicada

`.github/workflows/docker.yml` construye y publica `ghcr.io/<owner>/<repo>` cuando subes un tag
`v*` (también etiquetado `latest`) o lo lanzas a mano. Solo usa `GITHUB_TOKEN` con
`packages: write`. Para usar esa imagen en vez de construir en el servidor, cambia `build: .` por
`image: ghcr.io/<owner>/<repo>:<tag>` en `docker-compose.yml`.

## Sin Docker

```bash
uv sync --locked --no-dev --all-extras
uv run --env-file .env python -m tgbot
```

Como servicio de systemd, ejecuta `/ruta/al/repo/.venv/bin/python -m tgbot` con
`EnvironmentFile=/ruta/al/repo/.env`, `Restart=on-failure` y un `DATABASE_URL` que apunte a una
ruta escribible.

## Modo webhook

El long polling no necesita puertos de entrada y es el modo por defecto. Para webhooks:

1. Define `TELEGRAM_WEBHOOK_URL=https://bot.ejemplo.com/telegram-<aleatorio>` y
   `TELEGRAM_WEBHOOK_SECRET` (`openssl rand -hex 32`). Telegram solo entrega por HTTPS a los
   puertos 443, 80, 88 y 8443.
2. Descomenta la línea `ports` de `docker-compose.yml` (escucha en `127.0.0.1:8080`) y pon delante
   un proxy inverso con TLS, por ejemplo Caddy:

   ```text
   bot.ejemplo.com {
       reverse_proxy 127.0.0.1:8080
   }
   ```

El bot registra el webhook él mismo al arrancar. python-telegram-bot rechaza (403) las peticiones
que no traen la cabecera secreta.
