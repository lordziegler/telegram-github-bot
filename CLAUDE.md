# telegram-github-bot

Bot de Telegram que gestiona repositorios de GitHub. Python 3.11+, python-telegram-bot 22, httpx,
SQLite. Proyecto para una clase: toda la prosa está en español.

## Comandos

- `make install` · `make lint` · `make typecheck` · `make test` · `make check` (los tres) · `make build`
- `make format` arregla el formato y lo que ruff pueda corregir solo.
- Los tests no usan red: `tests/conftest.py` simula la Bot API de Telegram (`FakeTelegram`, un
  `BaseRequest`) y GitHub (`FakeGitHub`, una tabla de rutas sobre `httpx.MockTransport`).

## Estructura

Ver `docs/architecture.md`. En corto: `domain.py` (reglas puras) · `config.py` · `log.py` ·
`store.py` (capa SQLite sobre el entorno) · `github.py` (cliente REST) · `bot.py` (registro +
dispatch) · `commands/*.py` (se importan solos, `@command`) · `locales/es.json` (todo el texto de
la interfaz).

## Idioma

- Docs, README, comentarios, docstrings, mensajes de configuración y logs: español.
- Identificadores del código: inglés. Textos de la interfaz: `locales/es.json`.
- Mensajes de commit: inglés. `LICENSE`: texto oficial del MIT, en inglés.

## Invariantes — respétalos

- Todo comando entra por `Runtime.dispatch`; regístralo con `@command`, nunca con un handler crudo
  de python-telegram-bot.
- Valida los valores del usuario con los validadores de `domain.py` antes de que lleguen a una ruta
  de la API de GitHub.
- Nunca registres en el log el texto de los mensajes ni secretos; los logs pasan por
  `JsonFormatter`, que los oculta.
- Las escrituras a GitHub (POST/PATCH) no se reintentan si la petición pudo haberse procesado.
- Lo irreversible o lo que se hace a nombre del dueño del token pasa por `ctx.confirm`.
- Los mensajes editados nunca ejecutan comandos (`allowed_updates` + `filters.UpdateType.MESSAGE`).
- Los admins, usuarios y repos del entorno no se pueden cambiar desde el chat (protección contra
  quedarse sin acceso).
- Cada comando nuevo necesita las claves `cmd.<nombre>.desc|usage|example` (un test lo exige).
- El valor de un secret: borrar el mensaje primero, cifrarlo enseguida, no repetirlo nunca en una
  respuesta, un log ni un `UserError`.
