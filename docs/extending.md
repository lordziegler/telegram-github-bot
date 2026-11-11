# Extending

## Add a command

1. Create `src/tgbot/commands/<area>.py` (or add to an existing module). Every module in that
   package is imported at startup, so nothing else needs to change.

   ```python
   from tgbot.bot import Ctx, command
   from tgbot.domain import Role, UserError, parse_number


   @command("reactions", role=Role.VIEWER)
   async def reactions(ctx: Ctx, text: str) -> str:
       if not text:
           raise UserError("usage")  # shows cmd.reactions.usage + example
       number = parse_number(text)  # validate before building a URL
       items = await ctx.gh.get(f"/repos/{ctx.repo}/issues/{number}/reactions")
       return ctx.t("reactions_count", number=number, count=len(items))
   ```

2. Add the texts to `src/tgbot/locales/es.json`: `cmd.reactions.desc` (shown in Telegram's `/`
   menu and in `/help`), `cmd.reactions.usage`, `cmd.reactions.example`, plus any message keys.
   Templates are HTML; parameters are escaped for you.

3. Add a test. `tests/conftest.py` has a harness with the real python-telegram-bot application,
   a fake Telegram API and a fake GitHub:

   ```python
   def test_reactions() -> None:
       async def scenario() -> None:
           h = await harness({("GET", "/repos/octo-org/api/issues/5/reactions"): [{}, {}]})
           await h.send(VIEWER, "/reactions 5")
           assert "2" in h.tg.last_text()

       asyncio.run(scenario())
   ```

   `test_help_fits_in_one_message_and_every_command_is_translated` fails if a locale key is missing.

What `@command` gives you: role check (`role=None` makes it public), per-user rate limit,
optional `per_minute=` limit, correlation ID, logging, and the translation of `UserError` and
GitHub errors into messages.

Tools inside a handler (`ctx`):

| | |
|---|---|
| `ctx.repo` | the user's selected repository (raises a helpful error if none) |
| `ctx.gh.get / send / page / log_tail` | GitHub calls with auth, retries and error mapping |
| `ctx.t(key, **params)` | translated HTML |
| `ctx.require(Role.ADMIN)` | extra check inside a command (see `/review`) |
| `ctx.confirm(prompt, action)` | Confirm/Cancel buttons; `action` runs only on Confirm by the same user |
| `ctx.paginate(render)` | ◀ ▶ buttons; `render(page)` returns `(text or Reply, has_next)` |
| `ctx.button(label, action)` | a button that answers with a new message |
| `ctx.date(iso)` | a GitHub timestamp in `DEFAULT_TIMEZONE` |

Rules: validate every user value with the parsers in `domain.py` before it reaches a URL; use
`ctx.confirm` for anything irreversible or done on someone's behalf; never log message text.

## Add a language

Copy `src/tgbot/locales/es.json` to `<code>.json`, translate the values (keep the `{placeholders}`
and the HTML tags), and set `BOT_LOCALE=<code>`.

## Another Git provider

Commands call `ctx.gh` (the GitHub client) directly. To support GitLab or Gitea, extract the
calls the commands make into a small interface, implement it per provider, and choose the
implementation in `main.py`. This was deliberately not done before a second provider exists.

## Future extensions

Left out of this version on purpose: deleting repositories, branches or releases; force pushes;
branch protection changes; merging PRs; notifications from GitHub webhooks (would bring back a
`GITHUB_WEBHOOK_SECRET` and an HTTP endpoint); multiple App installations (repositories across
several owners with one App). Destructive additions should be admin-only and use `ctx.confirm`.
