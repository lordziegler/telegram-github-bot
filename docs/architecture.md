# Arquitectura

La separación en capas más simple que mantiene aparte Telegram, GitHub y las reglas. Un módulo
por responsabilidad, sin frameworks ni contenedor de inyección de dependencias: los objetos se
crean una vez en `main.py` y se pasan hacia abajo.

```text
src/tgbot/
├── domain.py        reglas: Role, validadores de entrada, UserError, RateLimiter   (sin E/S)
├── config.py        entorno → Settings validados; informa de todos los problemas a la vez
├── log.py           logs JSON, ID de correlación, ocultación de secretos en el único punto de salida
├── store.py         capa SQLite: roles concedidos desde el chat, repos extra, repo seleccionado
├── github.py        cliente REST de GitHub: auth con PAT o App, reintentos, errores, paginación
├── bot.py           adaptador de Telegram: registro de comandos, dispatch, confirmaciones, páginas
├── commands/        un módulo por área; cada @command se registra solo
│   ├── general.py   /start /help /whoami /repos /repo
│   ├── issues.py    /issues /issue /issue_new /comment /close /reopen /label /assign
│   ├── pulls.py     /prs /pr /review /ci
│   ├── actions.py   /run /runs /workflows (+ cancelar, reintentar, aprobar despliegues)
│   ├── secrets.py   /secret
│   └── admin.py     /admin
├── locales/es.json  todos los textos que ve el usuario
└── main.py          montaje, polling o webhook, heartbeat, healthcheck
```

Correspondencia con las capas que se suelen pedir:

| Capa | Aquí |
|---|---|
| domain | `domain.py` |
| application (orquestación) | `bot.Runtime.dispatch` + las funciones de comando |
| adapters/telegram | `bot.py`, `commands/`, `locales/` |
| adapters/github | `github.py` |
| infrastructure/config, logging, persistence | `config.py`, `log.py`, `store.py` |

## Flujo de una petición

```text
Update de Telegram
  → CommandHandler (solo updates MESSAGE)            python-telegram-bot
  → Runtime.dispatch(cmd)                            bot.py — la única puerta de entrada
      1. ¿chat permitido? (privado, o en TELEGRAM_ALLOWED_CHAT_IDS) — si no, se ignora sin responder
      2. ID de correlación = "u<update_id>" en una ContextVar; aparece en cada línea de log
      3. límite de frecuencia: por usuario (RATE_LIMIT_REQUESTS_PER_MINUTE) y por comando (per_minute=)
      4. comprobación del rol mínimo del comando
      5. handler(ctx, text) → Reply (texto + botones opcionales)
      6. cualquier excepción → mensaje útil (UserError / GitHubError) o "error interno, ref uN"
  → reply_text (HTML); si supera MAX_MESSAGE_LENGTH pasa a texto plano en vez de cortar etiquetas
```

Los botones (confirmaciones, paginación, detalle de ejecuciones) solo llevan un token aleatorio
en `callback_data` (Telegram admite 64 bytes). La acción vive en memoria, ligada al usuario que la
creó, al rol mínimo y a una caducidad (5 minutos para confirmaciones, 30 para listas). En cada
pulsación el bot comprueba que quien pulsa es el dueño y vuelve a leer su rol.

## Decisiones tomadas donde el enunciado era ambiguo

- **El entorno es la base; SQLite es una capa encima.** Los admins de `TELEGRAM_ADMIN_USER_IDS`,
  los usuarios de `TELEGRAM_ALLOWED_USER_IDS` y los repos de `DEFAULT_REPOSITORIES` no se pueden
  quitar ni degradar desde el chat; así es imposible quedarse sin acceso. Lo que se añade con
  `/admin` va a SQLite.
- **Los usuarios de `TELEGRAM_ALLOWED_USER_IDS` empiezan como `viewer`.** Mínimo privilegio; los
  admins los promocionan.
- **Un grupo permitido no da acceso a sus miembros.** El chat tiene que estar permitido *y* el
  usuario tiene que tener un rol.
- **Matriz de roles.** `close`/`reopen` son de maintainer, con confirmación. `review approve` y
  `request_changes` son solo de admin porque GitHub las registra a nombre del dueño del token y
  pueden cumplir una protección de rama. `/run` es solo de admin: ejecuta código con los secretos
  del repositorio.
- **`/label` y `/assign` también quitan** (`-nombre`), en vez de añadir dos comandos más.
- **Los logs de una ejecución** se ven con un botón en `/runs`: jobs, pasos fallidos y las últimas
  20 líneas del log del primer job fallido, leído en streaming para que un log enorme nunca quede
  entero en memoria.
- **Secrets por Telegram**: solo admin y en privado; el mensaje se borra al llegar y el valor se
  cifra antes de pedir confirmación, así que en memoria solo queda cifrado. Ver `docs/security.md`.
- **Acciones de CI/CD como botones** en el detalle de `/runs`, solo para admins: cada botón es de un
  solo uso y es la confirmación de esa acción concreta (cancelar, reintentar, aprobar o rechazar
  un despliegue). El estado de la ejecución se vuelve a pedir al abrir el detalle.
- **`/ci`** combina los estados del commit con los check runs; los PAT fine-grained no pueden leer
  check runs, así que ante un 403 usa las ejecuciones de Actions del commit. Un número de hasta 6
  dígitos es un PR; de 7 a 40 caracteres hexadecimales, un SHA.
- **Reintentos.** Las lecturas se reintentan ante errores de red y 5xx con espera exponencial; las
  escrituras (POST/PATCH) solo cuando es seguro que GitHub no las procesó (no hubo conexión, o
  hubo límite de frecuencia), así que un timeout nunca crea un issue duplicado. La espera total se
  limita a 20 s porque hay una persona esperando; dentro de ese margen se respetan `retry-after`
  y `x-ratelimit-reset` de GitHub.
- **Los mensajes editados se ignoran** (`allowed_updates` los excluye y los handlers filtran por
  MESSAGE): editar `/issue_new …` no debe crear un segundo issue.
- **Se quitó `GITHUB_WEBHOOK_SECRET`** de la configuración: nada en esta versión recibe webhooks de
  GitHub. Se añadió `BOT_LOCALE` para que una traducción sea un archivo JSON nuevo.
- **Una sola instancia.** Telegram solo admite un consumidor de long polling por token, así que el
  estado en memoria (limitador, botones pendientes) basta. Los botones pendientes se pierden al
  reiniciar; el usuario repite el comando.

## Simplificaciones con un techo conocido

Marcadas en el código con comentarios `ponytail:`.

- Sin interfaces `Protocol`: GitHub tiene una sola implementación y los tests cambian su transporte
  HTTP (`httpx.MockTransport`). Añadir una interfaz cuando aparezca un segundo proveedor (GitLab,
  Gitea).
- Sin clases de entidad para issues/PRs: el JSON de GitHub se muestra directamente. Añadirlas
  cuando un segundo proveedor necesite una forma común.
- `/issues` descarta los pull requests después de paginar, así que una página puede salir corta.
  Usar la API de búsqueda si importa.
- El limitador y los botones pendientes son por proceso (ver «Una sola instancia»).
