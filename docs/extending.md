# Cómo extenderlo

## Añadir un comando

1. Crea `src/tgbot/commands/<area>.py` (o añádelo a un módulo existente). Todos los módulos de ese
   paquete se importan al arrancar, así que no hace falta cambiar nada más.

   ```python
   from tgbot.bot import Ctx, command
   from tgbot.domain import Role, UserError, parse_number


   @command("reactions", role=Role.VIEWER)
   async def reactions(ctx: Ctx, text: str) -> str:
       if not text:
           raise UserError("usage")  # muestra cmd.reactions.usage + el ejemplo
       number = parse_number(text)  # validar antes de construir una URL
       items = await ctx.gh.get(f"/repos/{ctx.repo}/issues/{number}/reactions")
       return ctx.t("reactions_count", number=number, count=len(items))
   ```

2. Añade los textos a `src/tgbot/locales/es.json`: `cmd.reactions.desc` (sale en el menú `/` de
   Telegram y en `/help`), `cmd.reactions.usage`, `cmd.reactions.example`, y las claves de los
   mensajes que uses (aquí `reactions_count`). Las plantillas son HTML; los parámetros se escapan
   solos.

3. Añade un test. `tests/conftest.py` tiene un arnés con la aplicación real de
   python-telegram-bot, una API de Telegram falsa y un GitHub falso:

   ```python
   def test_reactions() -> None:
       async def scenario() -> None:
           h = await harness({("GET", "/repos/octo-org/api/issues/5/reactions"): [{}, {}]})
           await h.send(VIEWER, "/reactions 5")
           assert "2" in h.tg.last_text()

       asyncio.run(scenario())
   ```

   `test_help_fits_in_one_message_and_every_command_is_translated` falla si falta alguna clave de
   idioma.

Lo que te da `@command`: comprobación de rol (`role=None` lo hace público), límite de frecuencia
por usuario, límite opcional `per_minute=`, ID de correlación, logs, y la traducción de `UserError`
y de los errores de GitHub a mensajes.

Herramientas dentro de un handler (`ctx`):

| | |
|---|---|
| `ctx.repo` | el repositorio seleccionado por el usuario (da un error claro si no hay ninguno) |
| `ctx.gh.get / send / page / log_tail` | llamadas a GitHub con autenticación, reintentos y errores traducidos |
| `ctx.t(clave, **params)` | HTML traducido |
| `ctx.require(Role.ADMIN)` | comprobación extra dentro de un comando (ver `/review`) |
| `ctx.confirm(texto, accion)` | botones Confirmar/Cancelar; `accion` solo se ejecuta si confirma el mismo usuario |
| `ctx.paginate(render)` | botones ◀ ▶; `render(pagina)` devuelve `(texto o Reply, hay_siguiente)` |
| `ctx.button(texto, accion, rol, once)` | botón que responde con un mensaje nuevo; `rol` exige más rol que el comando y `once=True` lo hace de un solo uso |
| `ctx.date(iso)` | una fecha de GitHub en `DEFAULT_TIMEZONE` |
| `ctx.message` | el mensaje de Telegram que lanzó el comando (p. ej. para borrarlo) |

Reglas: valida cada valor del usuario con los validadores de `domain.py` antes de que llegue a una
URL; usa `ctx.confirm` para todo lo irreversible o lo que se hace en nombre de otro; nunca
registres en el log el texto de los mensajes.

## Añadir un idioma

Copia `src/tgbot/locales/es.json` a `<codigo>.json`, traduce los valores (conserva los
`{marcadores}` y las etiquetas HTML) y define `BOT_LOCALE=<codigo>`.

## Otro proveedor Git

Los comandos llaman directamente a `ctx.gh` (el cliente de GitHub). Para soportar GitLab o Gitea,
extrae a una pequeña interfaz las llamadas que hacen los comandos, implementa una por proveedor y
elige la implementación en `main.py`. A propósito no se hizo antes de que exista un segundo
proveedor.

## Extensiones futuras

Fuera de esta versión a propósito: borrar repositorios, ramas o releases; force push; cambios en
protecciones de rama; fusionar PRs; notificaciones desde webhooks de GitHub (traería de vuelta un
`GITHUB_WEBHOOK_SECRET` y un endpoint HTTP); varias instalaciones de una App (repositorios de
varios propietarios con una sola App). Lo destructivo que se añada debe ser solo para admin y usar
`ctx.confirm`.
