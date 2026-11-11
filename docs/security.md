# Seguridad

## Control de acceso

- Solo los usuarios con rol pueden ejecutar algo más que `/start`, `/help` y `/whoami`. Un usuario
  desconocido recibe su ID de Telegram y cómo pedir acceso; para él nunca se llama a GitHub.
- En grupos, el chat tiene que estar en `TELEGRAM_ALLOWED_CHAT_IDS` y el usuario tiene que tener un
  rol. Los mensajes de otros grupos se ignoran sin responder.
- Todo comando pasa por una única función (`Runtime.dispatch`), que aplica la comprobación del
  chat, los límites de frecuencia y la comprobación de rol antes de ejecutar el handler. Un
  comando nuevo no puede saltársela.
- Los botones están ligados a quien los generó. Pulsar el botón de otro, uno caducado, o una
  confirmación después de haber perdido el rol no hace nada.
- Los admins del entorno no se pueden degradar ni expulsar desde el chat, así que un admin añadido
  en ejecución no puede apoderarse del bot ni dejarlo sin administradores.

## Permisos en GitHub

Concede lo mínimo (tabla en el [README](../README.md#3-darle-acceso-a-github-mínimo-privilegio)):
nada de escritura en Contents, ni Administration, ni Secrets. El bot solo gestiona los repositorios
de su lista, aunque el token vea más. Toda acción en GitHub queda registrada a nombre del dueño
del token o de la App, nunca del usuario de Telegram; el log JSON del bot registra qué usuario de
Telegram hizo qué (`user_id`, `command`, `cid`). Por eso `approve` / `request_changes` y `/run` son
solo de admin y con confirmación.

Para organizaciones es preferible una GitHub App: tokens de instalación de vida corta (1 hora),
instalación por repositorio y acciones atribuidas a la app.

## Tratamiento de la entrada

- Los nombres de repositorio, usuarios, refs, archivos de workflow, números e inputs de workflow se
  validan con patrones estrictos antes de ponerlos en una ruta de URL; se rechazan recorridos del
  tipo `owner/..`. Los nombres de etiqueta se codifican para URL.
- Al texto libre (títulos, cuerpos, comentarios) se le quitan los caracteres de control y se le
  aplican límites de longitud.
- Todo lo que viene de GitHub se escapa como HTML antes de enviarlo a Telegram.
- Ningún mensaje se convierte nunca en un comando de shell, `eval` o subproceso.

## Secretos

- Los secretos solo vienen del entorno. `.env`, `*.pem` y las bases de datos están en
  `.gitignore` y `.dockerignore`.
- Toda la salida de logs pasa por un único formateador que reemplaza cada valor secreto
  configurado y todo lo que tenga forma de token de bot de Telegram, de token de GitHub (`ghp_`,
  `ghs_`, `github_pat_`…) o de clave privada PEM. El log de peticiones de `httpx` (que incluiría el
  token del bot en la URL) se baja a WARNING.
- Los errores que escapan del bot (por ejemplo, `InvalidToken` de python-telegram-bot, cuyo
  mensaje contiene el token) se registran con ese formateador en vez de un traceback sin filtrar.
- Ante un fallo inesperado, el usuario ve «error interno, ref uN», nunca un stack trace.

## Límites contra el abuso

Límites de frecuencia por usuario y por comando; las llamadas a GitHub se reintentan dentro de un
margen de 20 s; las escrituras nunca se reintentan si GitHub pudo haberlas procesado (sin issues
duplicados).

## CI/CD

- `ci.yml` usa `pull_request` (no `pull_request_target`): los pull requests de forks corren con un
  token de solo lectura y sin secretos. `permissions: contents: read`.
- `docker.yml` solo tiene `packages: write` en el job que publica y usa `GITHUB_TOKEN`.
- `deploy.yml` es manual, está ligado al environment `production` (añade revisores obligatorios),
  usa una clave SSH dedicada limitada con `restrict`, comprueba la clave fijada del host y borra la
  clave al terminar. El usuario de despliegue está en el grupo `docker`, lo que equivale a root en
  ese servidor: usa un servidor dedicado a servicios de confianza.
- Las acciones de terceros están fijadas por SHA de commit.

## Si algo se filtra

Si encuentras una vulnerabilidad en tu despliegue, rota primero el token afectado (`/revoke` en
BotFather, el token de GitHub o la clave de la App) y después corrige.
