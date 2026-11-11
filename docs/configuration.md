# Configuración

Toda la configuración sale de variables de entorno (o de `.env`, que leen `make run` y Docker
Compose). Se valida al arrancar en `src/tgbot/config.py`: se listan todos los problemas a la vez y
el proceso termina con código 2.

## Telegram

| Variable | Obligatoria | Por defecto | Notas |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | sí | | De @BotFather. Debe tener la forma `<dígitos>:<secreto>`. |
| `TELEGRAM_ADMIN_USER_IDS` | sí | | IDs de usuario separados por comas. Siempre `admin`; no se pueden degradar ni expulsar desde el chat. |
| `TELEGRAM_ALLOWED_USER_IDS` | no | | Usuarios autorizados desde el inicio como `viewer`. No se pueden expulsar desde el chat; sí promocionar. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | no | | Grupos donde responde el bot (IDs negativos). Los chats privados funcionan siempre. Ser miembro no da acceso por sí solo. |
| `TELEGRAM_WEBHOOK_URL` | no | | Activa el modo webhook. Debe ser `https://`. Su ruta es la que escucha el bot (puerto 8080). |
| `TELEGRAM_WEBHOOK_SECRET` | con webhook | | De 1 a 256 caracteres entre `A-Z a-z 0-9 _ -`. Telegram lo envía en `X-Telegram-Bot-Api-Secret-Token`; las peticiones sin él se rechazan. |

## GitHub

Define `GITHUB_TOKEN` o las tres variables `GITHUB_APP_*`. Si están las dos cosas, se usa la App.

| Variable | Notas |
|---|---|
| `GITHUB_TOKEN` | PAT fine-grained (permisos en el [README](../README.md#3-darle-acceso-a-github-mínimo-privilegio)). Un PAT clásico también funciona, pero concede mucho más de lo necesario. |
| `GITHUB_APP_ID` | ID de la App o client ID (se usa como emisor del JWT). |
| `GITHUB_APP_PRIVATE_KEY` | El texto PEM (se aceptan `\n` escritos tal cual, para `.env` de una línea) o la ruta al archivo `.pem`. |
| `GITHUB_APP_INSTALLATION_ID` | ID numérico de la instalación. Los tokens de instalación se piden cuando hacen falta y se renuevan 5 minutos antes de que caduquen (duran 1 hora). Requiere el extra `github-app`. |

## Aplicación

| Variable | Por defecto | Notas |
|---|---|---|
| `DEFAULT_REPOSITORIES` | | `owner/repo,owner/repo`. Fijos; se pueden añadir más con `/admin repo add`. Los duplicados se ignoran. |
| `DEFAULT_TIMEZONE` | `UTC` (`.env.example` pone `America/Bogota`) | Zona IANA para mostrar fechas. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `DATABASE_URL` | en memoria | `sqlite:///relativa.db` o `sqlite:////absoluta.db`; se crean los directorios padre. En memoria, lo cambiado desde Telegram se pierde al reiniciar (se avisa en el log). Compose usa `/data/bot.db`. |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `30` | Por usuario, sumando todos los comandos. Algunos comandos tienen además su propio límite (`/run` 3, `/issue_new` 5, `/review` 5, `/comment` 10). |
| `MAX_MESSAGE_LENGTH` | `4000` | De 500 a 4096. Las respuestas más largas se envían como texto plano, cortadas a esta longitud. |
| `BOT_LOCALE` | `es` | Nombre de un archivo de `src/tgbot/locales/`. |

## Secretos

Nunca subas `.env` a git. En los logs, cada valor secreto configurado y todo lo que tenga forma
de token de Telegram o de GitHub o de clave privada PEM se reemplaza por `[REDACTED]`. Ver
[security.md](security.md).
