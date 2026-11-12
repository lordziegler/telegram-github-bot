# telegram-github-bot

Bot de Telegram autoalojado para trabajar con repositorios de GitHub desde un chat: consultar y
crear issues, comentar, etiquetar, asignar, revisar pull requests, ver la CI, lanzar workflows de
GitHub Actions, aprobar despliegues (CD) y gestionar los secrets del repositorio. El acceso está limitado a una lista de usuarios de Telegram con tres roles.

Todo lo que es tuyo (tokens, IDs de usuario, repositorios) vive en variables de entorno: nadie
tiene que tocar el código para montar su propia instancia. La interfaz del bot está en español y
todos sus textos están en [`src/tgbot/locales/es.json`](src/tgbot/locales/es.json).

| Rol | Puede |
|---|---|
| `viewer` | `/repos` `/repo` `/issues` `/issue` `/prs` `/pr` `/ci` `/workflows` `/runs` |
| `maintainer` | lo anterior + `/issue_new` `/comment` `/label` `/assign` `/close` `/reopen` `/review … comment` |
| `admin` | lo anterior + `/review … approve\|request_changes` `/run` `/secret` `/admin …`, y en el detalle de cada ejecución de `/runs`: cancelar, reintentar lo fallido y aprobar o rechazar un despliegue pendiente |

`/start`, `/help` y `/whoami` funcionan para cualquiera (un usuario desconocido ve su ID de
Telegram para pedir acceso). Cerrar, reabrir, aprobar, pedir cambios, lanzar workflows y
`/admin deny|role|repo remove` piden confirmación con botones que solo puede pulsar quien lanzó
el comando.

## 1. Clonar

```bash
git clone https://github.com/<tu-usuario>/telegram-github-bot.git
cd telegram-github-bot
cp .env.example .env
```

## 2. Crear el bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather), envía `/newbot` y copia el token en
   `TELEGRAM_BOT_TOKEN`.
2. Necesitas tu ID de usuario de Telegram para `TELEGRAM_ADMIN_USER_IDS`. El propio bot te lo dice:
   arráncalo una vez con un valor provisional (`TELEGRAM_ADMIN_USER_IDS=1`), envíale `/start`,
   copia en `.env` el ID que te responde y reinícialo.
3. Para usarlo en un grupo, añade el bot al grupo y pon el ID del chat del grupo (un número
   negativo) en `TELEGRAM_ALLOWED_CHAT_IDS`. Los usuarios siguen necesitando acceso individual.

## 3. Darle acceso a GitHub (mínimo privilegio)

**Opción A: personal access token fine-grained** (lo más sencillo). En GitHub → Settings →
Developer settings → Fine-grained tokens: elige el propietario (resource owner), *Only select
repositories*, y concede exactamente:

| Permiso de repositorio | Acceso | Lo usan |
|---|---|---|
| Metadata | Read (obligatorio) | todo |
| Issues | Read and write | `/issues` `/issue` `/issue_new` `/comment` `/close` `/reopen` `/label` `/assign` |
| Pull requests | Read and write | `/prs` `/pr` `/review` |
| Commit statuses | Read | `/ci` |
| Actions | Read and write | `/workflows` `/runs` `/ci`; escritura para `/run`, cancelar y reintentar (usa Read si no las vas a usar) |
| Secrets | Read and write | `/secret` (opcional: sin él, ese comando da error de permisos) |
| Deployments | Read and write | aprobar o rechazar despliegues pendientes (opcional) |

Ponlo en `GITHUB_TOKEN`. Un token fine-grained cubre un solo propietario (usuario u organización)
y no tiene acceso a la API de check runs, así que `/ci` muestra en su lugar los estados del commit
y las ejecuciones de Actions.

**Opción B: GitHub App** (recomendada para organizaciones; las acciones aparecen a nombre de la
app, no de una persona). Crea una app con los mismos permisos más *Checks: Read*, instálala en los
repositorios, genera una clave privada y define `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (el
texto PEM o la ruta al `.pem`) y `GITHUB_APP_INSTALLATION_ID` (el número al final de la URL de
ajustes de la instalación). Instala el extra `github-app` (la imagen Docker ya lo incluye). Si hay
token y app configurados, se usa la app.

Sea cual sea la identidad, **toda acción en GitHub se hace a nombre de ese token o app**, no del
usuario de Telegram. Por eso aprobar, pedir cambios, lanzar workflows y tocar secrets es solo para
admins. Aprobar un despliegue solo funciona si el dueño del token es revisor del environment (una
GitHub App no puede serlo).

## 4. Configurar

Rellena `.env`. Obligatorio: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_USER_IDS` y las credenciales
de GitHub. Cada variable está documentada en [`.env.example`](.env.example) y en
[docs/configuration.md](docs/configuration.md). Si falta algo o un valor no es válido, el bot no
arranca y lista exactamente qué corregir.

## 5. Ejecutar en local

Con Docker Compose (el estado se guarda en el volumen `bot-data`):

```bash
docker compose up -d --build
docker compose logs -f bot
```

O con Python 3.11+ y [uv](https://docs.astral.sh/uv/):

```bash
make install
make run        # lee .env
```

Por defecto el bot usa long polling, así que no necesita abrir ningún puerto. El modo webhook está
en [docs/deployment.md](docs/deployment.md#modo-webhook).

## 6. Desplegar

Ejecuta el mismo archivo de Compose en cualquier servidor con Docker. El workflow
[`deploy.yml`](.github/workflows/deploy.yml) lo despliega o reinicia por SSH desde la pestaña
Actions, o solo tras cada CI verde en `main` si activas `AUTO_DEPLOY` (CD), esperando tu
aprobación, que puedes dar desde el propio bot. Configuración, secretos y alternativas en [docs/deployment.md](docs/deployment.md).
GitHub Actions sirve para *desplegar* el bot, no para alojarlo: los jobs tienen tiempo limitado y
su disco se descarta, lo que borraría los roles y repositorios añadidos desde Telegram.

### Secrets y CI/CD desde Telegram

```text
/secret                               # nombres de los secrets (GitHub nunca devuelve valores)
/secret set DEPLOY_TOKEN abc123       # solo en chat privado: el bot borra tu mensaje y lo cifra
/secret delete DEPLOY_TOKEN           # pide confirmación
/workflows                            # workflows del repo y su estado
/runs deploy.yml                      # pulsa #N: jobs, log y botones de admin
```

El valor de un secret pasa por los servidores de Telegram antes de que el bot lo borre: úsalo para
valores que puedas rotar y lee [docs/security.md](docs/security.md#secrets-enviados-por-telegram).

## 7. Añadir repositorios y usuarios

Desde Telegram, como admin:

```text
/admin repo add octo-org/api          # comprueba que el token llega al repo
/repo octo-org/api                    # lo selecciona para tus comandos
/admin allow 123456789                # usuario nuevo, empieza como viewer
/admin role 123456789 maintainer
/admin                                # lista usuarios y repositorios
```

Los usuarios y repositorios definidos en variables de entorno están fijados: no se pueden quitar
ni degradar desde el chat, así que un admin nunca puede quedarse fuera.

## 8. Añadir un comando

Crea un módulo en `src/tgbot/commands/`; se carga automáticamente:

```python
from tgbot.bot import Ctx, command
from tgbot.domain import Role


@command("labels", role=Role.VIEWER)
async def labels(ctx: Ctx, text: str) -> str:
    items = await ctx.gh.get(f"/repos/{ctx.repo}/labels", per_page=100)
    return ", ".join(label["name"] for label in items) or "-"
```

Añade `cmd.labels.desc`, `cmd.labels.usage` y `cmd.labels.example` al archivo de idioma, y un
test. La autorización, el límite de frecuencia, los mensajes de error y los logs se aplican solos.
Más en [docs/extending.md](docs/extending.md).

## Desarrollo

```bash
make install     # uv sync --locked --all-extras
make lint        # ruff check + comprobación de formato
make typecheck   # mypy --strict
make test        # pytest: unitarios + integración (Telegram y GitHub simulados, sin red)
make build       # imagen Docker
```

La CI ejecuta todo lo anterior con Python 3.11 y 3.14 y construye la imagen.

## Fuera de alcance (a propósito)

Borrar repositorios o releases, force push, cambiar protecciones de rama, fusionar PRs y las
notificaciones desde webhooks de GitHub quedan fuera de esta versión. Ver
[docs/extending.md](docs/extending.md#extensiones-futuras).

## Documentación

- [Arquitectura y decisiones de diseño](docs/architecture.md)
- [Referencia de configuración](docs/configuration.md)
- [Despliegue](docs/deployment.md)
- [Seguridad](docs/security.md)
- [Cómo extenderlo](docs/extending.md)

Licencia: [MIT](LICENSE) (el texto legal se mantiene en inglés, que es su versión oficial).
