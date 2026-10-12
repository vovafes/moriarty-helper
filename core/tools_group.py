"""
One shared slash group, /tools.

Discord allows 100 top-level slash commands per bot and this bot is close to
that limit, so the commands of the panel-driven modules (todo, vouch, mini-games,
support, team ...) live as subcommands of a single group instead of 17 new
top-level entries. A cog marks a method with @tool(name, description) and calls
install(bot, self) in cog_load / uninstall(bot, self) in cog_unload.
"""

from discord import app_commands

TOOLS = app_commands.Group(name="tools", description="Дополнительные команды сервера", guild_only=True)


def tool(name: str, description: str):
    def deco(fn):
        fn.__tool__ = (name, description)
        return fn
    return deco


def _marked(cog):
    for attr in dir(type(cog)):
        fn = getattr(cog, attr, None)
        meta = getattr(fn, "__tool__", None)
        if meta and callable(fn):
            yield meta, fn


def install(bot, cog) -> dict[str, app_commands.Command]:
    """Attach the cog's marked methods to /tools; returns name -> Command (for autocomplete hooks)."""
    if bot.tree.get_command("tools") is None:
        bot.tree.add_command(TOOLS)
    added = {}
    for (name, description), fn in _marked(cog):
        cmd = app_commands.Command(name=name, description=description, callback=fn)
        TOOLS.add_command(cmd, override=True)
        added[name] = cmd
    return added


def uninstall(cog) -> None:
    for (name, _), _fn in _marked(cog):
        TOOLS.remove_command(name)
