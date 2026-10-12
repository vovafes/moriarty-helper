"""
Community tools (ported from the AlphaCloud panel):

* Embed creator -- build and post an embed from the panel.
* Changelog     -- publish update notes to a channel.
* Newsletter    -- members opt in with a button, announcements go to their DMs.
* To-do         -- /todo creates a task card with a "Готово" button.
* Partnerships  -- list of partner servers, posted as an embed.
* Vouches       -- /vouch: members say whether someone is honest or a scam.
* Mini-games    -- coinflip, roulette, blackjack, daily bonus and a counting channel.
"""

import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from core import db, embeds, modules
from core.tools_group import install as install_tools, tool, uninstall as uninstall_tools

db.register_schema("""
CREATE TABLE IF NOT EXISTS changelog_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, title TEXT NOT NULL,
    body TEXT NOT NULL, author TEXT, ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS news_subs (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS news_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, title TEXT, body TEXT,
    sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, author TEXT, ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS todo_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER, message_id INTEGER,
    text TEXT NOT NULL, assignee_id INTEGER, creator_id INTEGER, done INTEGER NOT NULL DEFAULT 0,
    done_by INTEGER, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS todo_msg ON todo_tasks (message_id);
CREATE TABLE IF NOT EXISTS partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, invite TEXT,
    rep TEXT, note TEXT, ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS vouches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
    voter_id INTEGER NOT NULL, positive INTEGER NOT NULL, comment TEXT, ts INTEGER NOT NULL,
    UNIQUE (guild_id, target_id, voter_id)
);
CREATE TABLE IF NOT EXISTS mg_wallet (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, balance INTEGER NOT NULL, last_daily INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS mg_counting (
    guild_id INTEGER PRIMARY KEY, current INTEGER NOT NULL DEFAULT 0, last_user INTEGER, best INTEGER NOT NULL DEFAULT 0
);
""")

COLOR = 0xBE1FB6

EMBED_CREATOR = modules.register(modules.Module(
    key="embed_creator", title="Конструктор эмбедов", icon="🧱", category="tools", toggleable=False,
    description="Соберите красивое сообщение и отправьте его от имени бота в любой канал.",
    fields=[],
))
CHANGELOG = modules.register(modules.Module(
    key="changelog", title="Список изменений", icon="📰", category="community", default_enabled=False,
    description="Публикуйте обновления и изменения для своего сообщества.",
    fields=[
        {"key": "channel", "label": "Канал изменений", "type": "channel", "kind": "text", "default": None},
        {"key": "ping_role", "label": "Роль для упоминания", "type": "role", "default": None},
        {"key": "manager_roles", "label": "Роли для команды /changelog", "type": "roles", "default": []},
        {"key": "color", "label": "Цвет эмбеда", "type": "text", "default": "#BE1FB6", "help": "HEX, например #BE1FB6"},
        {"key": "thumbnail", "label": "URL миниатюры", "type": "text", "default": ""},
    ],
    tables=[{"id": "history", "title": "Опубликовано", "columns": [
        {"key": "title", "label": "Заголовок"}, {"key": "author", "label": "Автор"}, {"key": "ts", "label": "Когда", "format": "time"}]}],
))
NEWSLETTER = modules.register(modules.Module(
    key="newsletter", title="Рассылка", icon="📬", category="community", default_enabled=False,
    description="Подписавшиеся участники получают объявления сервера в личные сообщения.",
    fields=[
        {"key": "channel", "label": "Канал подписки", "type": "channel", "kind": "text", "default": None, "group": "Подписка"},
        {"key": "panel_title", "label": "Заголовок панели подписки", "type": "text", "default": "Рассылка сервера", "group": "Подписка"},
        {"key": "panel_text", "label": "Текст панели подписки", "type": "longtext", "group": "Подписка",
         "default": "Нажмите кнопку, чтобы получать важные объявления сервера в личные сообщения. Отписаться можно там же."},
        {"key": "color", "label": "Акцентный цвет", "type": "text", "default": "#BE1FB6", "group": "Подписка"},
    ],
    tables=[{"id": "history", "title": "История рассылок", "columns": [
        {"key": "title", "label": "Заголовок"}, {"key": "sent", "label": "Доставлено"}, {"key": "failed", "label": "Не дошло"},
        {"key": "ts", "label": "Когда", "format": "time"}]}],
))
TODO = modules.register(modules.Module(
    key="todo", title="Задачи", icon="✅", category="tools", default_enabled=False,
    description="Создавайте задачи командой /todo: карточка появится в канале с кнопкой «Готово».",
    fields=[
        {"key": "channel", "label": "Канал задач", "type": "channel", "kind": "text", "default": None},
        {"key": "manager_roles", "label": "Кто может создавать задачи", "type": "roles", "default": [], "help": "Пусто — только администраторы"},
        {"key": "color", "label": "Цвет эмбеда", "type": "text", "default": "#BE23B6"},
        {"key": "auto_delete", "label": "Удалять выполненные задачи", "type": "bool", "default": False,
         "help": "Карточка исчезает из канала, как только задачу отметили выполненной"},
    ],
    tables=[{"id": "tasks", "title": "Задачи", "columns": [
        {"key": "id", "label": "#"}, {"key": "text", "label": "Задача"}, {"key": "assignee", "label": "Исполнитель"},
        {"key": "status", "label": "Статус"}, {"key": "ts", "label": "Создана", "format": "time"}]}],
))
PARTNERSHIP = modules.register(modules.Module(
    key="partnership", title="Партнёрства", icon="🤝", category="community", default_enabled=False,
    description="Список партнёрских серверов: добавляйте, убирайте и публикуйте красивым эмбедом.",
    fields=[
        {"key": "notify_channel", "label": "Канал уведомлений", "type": "channel", "kind": "text", "default": None},
        {"key": "partners_channel", "label": "Канал партнёров", "type": "channel", "kind": "text", "default": None},
        {"key": "title", "label": "Заголовок списка", "type": "text", "default": "Наши партнёры"},
        {"key": "description", "label": "Описание", "type": "longtext", "default": ""},
    ],
    tables=[{"id": "list", "title": "Партнёры", "columns": [
        {"key": "id", "label": "#"}, {"key": "name", "label": "Сервер"}, {"key": "invite", "label": "Приглашение"},
        {"key": "rep", "label": "Представитель"}, {"key": "ts", "label": "С", "format": "time"}]}],
))
VOUCHER = modules.register(modules.Module(
    key="voucher", title="Система отзывов", icon="🛡️", category="community", default_enabled=False,
    description="Участники голосуют командой /vouch, честный человек (магазин, сервер) или скам.",
    fields=[
        {"key": "channel", "label": "Канал отзывов", "type": "channel", "kind": "text", "default": None, "help": "Пусто — не публиковать"},
        {"key": "min_account_days", "label": "Минимальный возраст аккаунта голосующего, дней", "type": "number", "default": 7, "min": 0, "max": 365},
    ],
    tables=[{"id": "recent", "title": "Последние отзывы", "columns": [
        {"key": "target", "label": "О ком"}, {"key": "voter", "label": "Кто"}, {"key": "verdict", "label": "Вердикт"},
        {"key": "comment", "label": "Комментарий"}, {"key": "ts", "label": "Когда", "format": "time"}]}],
))
MINIGAMES = modules.register(modules.Module(
    key="minigames", title="Мини-игры", icon="🎲", category="community", default_enabled=False,
    description="/blackjack, /roulette, /coinflip, /daily, /games_top и канал-счётчик (Counting). Своя валюта, отдельная от фишек семьи.",
    fields=[
        {"key": "channels", "label": "Разрешённые каналы", "type": "channels", "kind": "text", "default": [], "help": "Пусто — везде"},
        {"key": "start_balance", "label": "Стартовый баланс", "type": "number", "default": 500, "min": 0, "max": 1000000},
        {"key": "daily_amount", "label": "Ежедневный бонус", "type": "number", "default": 200, "min": 0, "max": 1000000},
        {"key": "max_bet", "label": "Максимальная ставка", "type": "number", "default": 5000, "min": 1, "max": 10000000},
        {"key": "counting_channel", "label": "Канал для Counting", "type": "channel", "kind": "text", "default": None,
         "help": "Участники по очереди пишут числа по порядку: 1, 2, 3… Ошибка сбрасывает счёт"},
    ],
    tables=[{"id": "top", "title": "Богатейшие", "columns": [{"key": "user", "label": "Участник"}, {"key": "balance", "label": "Баланс"}]}],
))


# ── pure helpers (unit-tested) ──────────────────────────────────────────────

def parse_color(text: str | None, default: int = COLOR) -> int:
    s = (text or "").strip().lstrip("#")
    try:
        return int(s, 16) if len(s) == 6 else default
    except ValueError:
        return default


def card_value(card: int) -> int:
    """Blackjack card 1..13 -> points (ace counted as 11 here, fixed in hand_value)."""
    return 11 if card == 1 else min(card, 10)


def hand_value(cards: list[int]) -> int:
    total = sum(card_value(c) for c in cards)
    aces = sum(1 for c in cards if c == 1)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def card_name(card: int) -> str:
    return {1: "A", 11: "J", 12: "Q", 13: "K"}.get(card, str(card))


def coinflip_win(rng: random.Random, pick: str) -> tuple[bool, str]:
    side = rng.choice(["heads", "tails"])
    return side == pick, side


ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def roulette_payout(number: int, pick: str, bet: int) -> int:
    """Net result for the bet (positive = win, negative = loss). pick: red | black | green | even | odd | 0..36."""
    color = "green" if number == 0 else ("red" if number in ROULETTE_RED else "black")
    if pick == "green":
        return bet * 14 if number == 0 else -bet
    if pick in ("red", "black"):
        return bet if color == pick else -bet
    if pick in ("even", "odd"):
        return bet if number != 0 and (number % 2 == 0) == (pick == "even") else -bet
    if pick.isdigit():
        return bet * 35 if int(pick) == number else -bet
    return -bet


def counting_step(current: int, last_user: int | None, user: int, text: str) -> tuple[str, int]:
    """('ok' | 'wrong' | 'same_user' | 'ignore', new_current)."""
    t = (text or "").strip()
    if not t.isdigit():
        return "ignore", current
    if user == last_user:
        return "same_user", 0
    if int(t) != current + 1:
        return "wrong", 0
    return "ok", current + 1


# ── wallet ──────────────────────────────────────────────────────────────────

def wallet(guild_id: int, user_id: int, start: int) -> dict:
    rows = db.query("SELECT * FROM mg_wallet WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    if rows:
        return dict(rows[0])
    db.execute("INSERT OR IGNORE INTO mg_wallet (guild_id, user_id, balance) VALUES (?,?,?)", (guild_id, user_id, start))
    return {"guild_id": guild_id, "user_id": user_id, "balance": start, "last_daily": 0}


def add_balance(guild_id: int, user_id: int, delta: int) -> None:
    db.execute("UPDATE mg_wallet SET balance=MAX(0, balance+?) WHERE guild_id=? AND user_id=?", (delta, guild_id, user_id))


# ── views ───────────────────────────────────────────────────────────────────

class NewsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Подписаться / отписаться", style=discord.ButtonStyle.primary, custom_id="news:toggle", emoji="📬")
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid, uid = interaction.guild_id, interaction.user.id
        subscribed = await db.run(db.query, "SELECT 1 FROM news_subs WHERE guild_id=? AND user_id=?", (gid, uid))
        if subscribed:
            await db.run(db.execute, "DELETE FROM news_subs WHERE guild_id=? AND user_id=?", (gid, uid))
            return await interaction.response.send_message("📭 Вы отписались от рассылки.", ephemeral=True)
        await db.run(db.execute, "INSERT OR IGNORE INTO news_subs (guild_id, user_id, ts) VALUES (?,?,?)", (gid, uid, int(time.time())))
        await interaction.response.send_message("📬 Готово! Объявления будут приходить вам в личные сообщения.", ephemeral=True)


class TodoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Готово", style=discord.ButtonStyle.success, custom_id="todo:done", emoji="✅")
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await db.run(db.query, "SELECT * FROM todo_tasks WHERE message_id=? AND guild_id=?", (interaction.message.id, interaction.guild_id))
        if not rows:
            return await interaction.response.send_message("Задача не найдена.", ephemeral=True)
        task = dict(rows[0])
        if task["done"]:
            return await interaction.response.send_message("Уже выполнена.", ephemeral=True)
        if task["assignee_id"] and interaction.user.id != task["assignee_id"] and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("Это задача другого человека.", ephemeral=True)
        await db.run(db.execute, "UPDATE todo_tasks SET done=1, done_by=? WHERE id=?", (interaction.user.id, task["id"]))
        cfg = modules.get_config(interaction.guild_id, "todo")
        if cfg["auto_delete"]:
            await interaction.response.defer()
            try:
                await interaction.message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            return
        e = interaction.message.embeds[0] if interaction.message.embeds else embeds.make("Задача")
        e.color = 0x3BA55D
        e.add_field(name="Выполнено", value=interaction.user.mention, inline=False)
        await interaction.response.edit_message(embed=e, view=None)


class BlackjackView(discord.ui.View):
    def __init__(self, owner_id: int, guild_id: int, bet: int):
        super().__init__(timeout=120)
        self.owner_id, self.guild_id, self.bet = owner_id, guild_id, bet
        self.player = [random.randint(1, 13), random.randint(1, 13)]
        self.dealer = [random.randint(1, 13), random.randint(1, 13)]
        self.over = False

    def render(self, reveal: bool = False, footer: str = "") -> discord.Embed:
        dealer = " ".join(card_name(c) for c in self.dealer) if reveal else f"{card_name(self.dealer[0])} ?"
        e = embeds.make("🃏 Блэкджек", color=COLOR)
        e.add_field(name=f"Вы ({hand_value(self.player)})", value=" ".join(card_name(c) for c in self.player))
        e.add_field(name=f"Дилер ({hand_value(self.dealer) if reveal else card_value(self.dealer[0])})", value=dealer)
        e.add_field(name="Ставка", value=str(self.bet), inline=False)
        if footer:
            e.description = footer
        return e

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Это не ваша игра.", ephemeral=True)
            return False
        return True

    async def finish(self, interaction: discord.Interaction, delta: int, text: str):
        self.over = True
        self.stop()
        await db.run(add_balance, self.guild_id, self.owner_id, delta)
        await interaction.response.edit_message(embed=self.render(True, text), view=None)

    @discord.ui.button(label="Ещё", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.append(random.randint(1, 13))
        if hand_value(self.player) > 21:
            return await self.finish(interaction, -self.bet, f"💥 Перебор. Вы потеряли **{self.bet}**.")
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Хватит", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        while hand_value(self.dealer) < 17:
            self.dealer.append(random.randint(1, 13))
        p, d = hand_value(self.player), hand_value(self.dealer)
        if d > 21 or p > d:
            return await self.finish(interaction, self.bet, f"🎉 Победа! Вы выиграли **{self.bet}**.")
        if p == d:
            return await self.finish(interaction, 0, "🤝 Ничья, ставка возвращена.")
        await self.finish(interaction, -self.bet, f"😔 Дилер выиграл. Вы потеряли **{self.bet}**.")

    async def on_timeout(self):
        if not self.over:
            await db.run(add_balance, self.guild_id, self.owner_id, -self.bet)


# ── cog ─────────────────────────────────────────────────────────────────────

class CommunityTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(NewsView())
        self.bot.add_view(TodoView())
        install_tools(self.bot, self)

    async def cog_unload(self):
        uninstall_tools(self)

    # to-do -------------------------------------------------------------

    @tool("todo", "Создать задачу")
    @app_commands.describe(text="Что нужно сделать", assignee="Исполнитель")
    async def todo(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 500], assignee: discord.Member | None = None):
        cfg = modules.get_config(interaction.guild_id, "todo")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Задачи выключены.", ephemeral=True)
        allowed = set(cfg["manager_roles"])
        if not interaction.user.guild_permissions.administrator and not (allowed & {r.id for r in interaction.user.roles}):
            return await interaction.response.send_message("Создавать задачи могут только менеджеры.", ephemeral=True)
        msg = await create_task(interaction.guild, cfg, text, assignee.id if assignee else None, interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=True)

    # vouches -----------------------------------------------------------

    @tool("vouch", "Оставить отзыв о человеке: честный или скам")
    @app_commands.describe(target="О ком", verdict="Вердикт", comment="Комментарий")
    @app_commands.choices(verdict=[app_commands.Choice(name="Честный ✅", value="yes"), app_commands.Choice(name="Скам ⛔", value="no")])
    async def vouch(self, interaction: discord.Interaction, target: discord.Member, verdict: app_commands.Choice[str],
                    comment: app_commands.Range[str, 0, 300] = ""):
        cfg = modules.get_config(interaction.guild_id, "voucher")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Система отзывов выключена.", ephemeral=True)
        if target.id == interaction.user.id or target.bot:
            return await interaction.response.send_message("Так нельзя.", ephemeral=True)
        age = (discord.utils.utcnow() - interaction.user.created_at).days
        if age < cfg["min_account_days"]:
            return await interaction.response.send_message(f"Ваш аккаунт должен быть старше {cfg['min_account_days']} дн.", ephemeral=True)
        positive = verdict.value == "yes"
        await db.run(db.execute, "INSERT INTO vouches (guild_id, target_id, voter_id, positive, comment, ts) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(guild_id, target_id, voter_id) DO UPDATE SET positive=excluded.positive, comment=excluded.comment, ts=excluded.ts",
                     (interaction.guild_id, target.id, interaction.user.id, int(positive), comment, int(time.time())))
        yes, no = await db.run(vouch_counts, interaction.guild_id, target.id)
        ch = interaction.guild.get_channel(cfg["channel"]) if cfg["channel"] else None
        if ch is not None:
            e = embeds.make("Новый отзыв", f"{interaction.user.mention} → {target.mention}: **{'честный ✅' if positive else 'скам ⛔'}**"
                            + (f"\n> {comment}" if comment else ""), color=0x3BA55D if positive else 0xED4245)
            e.set_footer(text=f"Всего: ✅ {yes} · ⛔ {no}")
            try:
                await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.response.send_message(f"Спасибо! У {target.display_name}: ✅ {yes} · ⛔ {no}", ephemeral=True)

    @tool("vouches", "Отзывы о человеке")
    async def vouches(self, interaction: discord.Interaction, target: discord.Member):
        yes, no = await db.run(vouch_counts, interaction.guild_id, target.id)
        await interaction.response.send_message(embed=embeds.make(f"Отзывы о {target.display_name}", f"✅ Честный: **{yes}**\n⛔ Скам: **{no}**"), ephemeral=True)

    # changelog ---------------------------------------------------------

    @tool("changelog", "Опубликовать список изменений")
    @app_commands.describe(title="Заголовок", text="Что изменилось")
    async def changelog(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 200], text: app_commands.Range[str, 1, 3500]):
        cfg = modules.get_config(interaction.guild_id, "changelog")
        if not cfg["enabled"]:
            return await interaction.response.send_message("Модуль выключен.", ephemeral=True)
        if not interaction.user.guild_permissions.administrator and not (set(cfg["manager_roles"]) & {r.id for r in interaction.user.roles}):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)
        await interaction.response.send_message(await publish_changelog(interaction.guild, cfg, title, text, interaction.user.display_name), ephemeral=True)

    # mini-games --------------------------------------------------------

    async def _game_gate(self, interaction: discord.Interaction, bet: int | None = None):
        """Returns (cfg, wallet) or None after answering the interaction."""
        cfg = modules.get_config(interaction.guild_id, "minigames")
        if not cfg["enabled"]:
            await interaction.response.send_message("Мини-игры выключены.", ephemeral=True)
            return None
        if cfg["channels"] and interaction.channel_id not in cfg["channels"]:
            await interaction.response.send_message("Здесь играть нельзя.", ephemeral=True)
            return None
        w = await db.run(wallet, interaction.guild_id, interaction.user.id, cfg["start_balance"])
        if bet is not None:
            if bet > cfg["max_bet"]:
                await interaction.response.send_message(f"Максимальная ставка — {cfg['max_bet']}.", ephemeral=True)
                return None
            if bet > w["balance"]:
                await interaction.response.send_message(f"Не хватает средств: у вас {w['balance']}.", ephemeral=True)
                return None
        return cfg, w

    @tool("daily", "Ежедневный бонус мини-игр")
    async def daily(self, interaction: discord.Interaction):
        gate = await self._game_gate(interaction)
        if not gate:
            return
        cfg, w = gate
        now = int(time.time())
        if now - w["last_daily"] < 86400:
            return await interaction.response.send_message(f"Следующий бонус <t:{w['last_daily'] + 86400}:R>.", ephemeral=True)
        await db.run(db.execute, "UPDATE mg_wallet SET balance=balance+?, last_daily=? WHERE guild_id=? AND user_id=?",
                     (cfg["daily_amount"], now, interaction.guild_id, interaction.user.id))
        await interaction.response.send_message(f"🎁 +{cfg['daily_amount']}. Баланс: {w['balance'] + cfg['daily_amount']}")

    @tool("coinflip", "Подбросить монетку на ставку")
    @app_commands.choices(side=[app_commands.Choice(name="Орёл", value="heads"), app_commands.Choice(name="Решка", value="tails")])
    async def coinflip(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1], side: app_commands.Choice[str]):
        gate = await self._game_gate(interaction, bet)
        if not gate:
            return
        won, got = coinflip_win(random, side.value)
        await db.run(add_balance, interaction.guild_id, interaction.user.id, bet if won else -bet)
        label = "Орёл" if got == "heads" else "Решка"
        await interaction.response.send_message(f"🪙 Выпало: **{label}**. " + (f"Вы выиграли **{bet}**!" if won else f"Вы потеряли **{bet}**."))

    @tool("roulette", "Рулетка: красное, чёрное, зелёное, чёт/нечет или число")
    @app_commands.describe(pick="red, black, green, even, odd или число 0–36")
    async def roulette(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1], pick: str):
        pick = pick.strip().lower()
        if pick not in ("red", "black", "green", "even", "odd") and not (pick.isdigit() and 0 <= int(pick) <= 36):
            return await interaction.response.send_message("Ставьте на: red, black, green, even, odd или число 0–36.", ephemeral=True)
        gate = await self._game_gate(interaction, bet)
        if not gate:
            return
        number = random.randint(0, 36)
        delta = roulette_payout(number, pick, bet)
        await db.run(add_balance, interaction.guild_id, interaction.user.id, delta)
        await interaction.response.send_message(f"🎡 Выпало **{number}**. " + (f"Вы выиграли **{delta}**!" if delta > 0 else f"Вы потеряли **{-delta}**."))

    @tool("blackjack", "Блэкджек против дилера")
    async def blackjack(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        gate = await self._game_gate(interaction, bet)
        if not gate:
            return
        view = BlackjackView(interaction.user.id, interaction.guild_id, bet)
        if hand_value(view.player) == 21:
            await db.run(add_balance, interaction.guild_id, interaction.user.id, bet * 3 // 2)
            return await interaction.response.send_message(embed=view.render(True, f"🂡 Блэкджек! +**{bet * 3 // 2}**"))
        await interaction.response.send_message(embed=view.render(), view=view)

    @tool("games_top", "Богатейшие игроки мини-игр")
    async def games_top(self, interaction: discord.Interaction):
        rows = await db.run(db.query, "SELECT user_id, balance FROM mg_wallet WHERE guild_id=? ORDER BY balance DESC LIMIT 10", (interaction.guild_id,))
        lines = [f"**{i}.** <@{r['user_id']}> — {r['balance']}" for i, r in enumerate(rows, 1)] or ["Пока никого."]
        await interaction.response.send_message(embed=embeds.make("🏆 Топ мини-игр", "\n".join(lines)), allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        cfg = modules.get_config(message.guild.id, "minigames")
        if not cfg["enabled"] or not cfg["counting_channel"] or message.channel.id != cfg["counting_channel"]:
            return
        row = (await db.run(db.query, "SELECT * FROM mg_counting WHERE guild_id=?", (message.guild.id,)) or [None])[0]
        current, last_user = (row["current"], row["last_user"]) if row else (0, None)
        verdict, new = counting_step(current, last_user, message.author.id, message.content)
        if verdict == "ignore":
            return
        if verdict == "ok":
            await db.run(db.execute, "INSERT INTO mg_counting (guild_id, current, last_user, best) VALUES (?,?,?,?) "
                         "ON CONFLICT(guild_id) DO UPDATE SET current=excluded.current, last_user=excluded.last_user, best=MAX(best, excluded.current)",
                         (message.guild.id, new, message.author.id, new))
            try:
                await message.add_reaction("✅")
            except (discord.Forbidden, discord.HTTPException):
                pass
            return
        await db.run(db.execute, "INSERT INTO mg_counting (guild_id, current, last_user) VALUES (?,0,NULL) "
                     "ON CONFLICT(guild_id) DO UPDATE SET current=0, last_user=NULL", (message.guild.id,))
        try:
            await message.add_reaction("❌")
            await message.channel.send(f"{message.author.mention} {'написал(а) два числа подряд' if verdict == 'same_user' else 'ошибся(лась)'}. Счёт сброшен, начинаем с **1**.",
                                       allowed_mentions=discord.AllowedMentions(users=[message.author]))
        except (discord.Forbidden, discord.HTTPException):
            pass


# ── shared operations (used by commands and panel actions) ──────────────────

def vouch_counts(guild_id: int, target_id: int) -> tuple[int, int]:
    r = db.query("SELECT COALESCE(SUM(positive),0) AS y, COUNT(*)-COALESCE(SUM(positive),0) AS n FROM vouches WHERE guild_id=? AND target_id=?",
                 (guild_id, target_id))[0]
    return int(r["y"]), int(r["n"])


async def create_task(guild, cfg, text: str, assignee_id: int | None, creator_id: int | None) -> str:
    ch = guild.get_channel(cfg["channel"]) if cfg["channel"] else None
    if ch is None:
        raise modules.ValidationError("Сначала выберите канал задач и сохраните")
    tid = (await db.run(db.execute, "INSERT INTO todo_tasks (guild_id, channel_id, text, assignee_id, creator_id, ts) VALUES (?,?,?,?,?,?)",
                        (guild.id, ch.id, text, assignee_id, creator_id, int(time.time())))).lastrowid
    e = embeds.make(f"Задача #{tid}", text, color=parse_color(cfg["color"], 0xBE23B6))
    if assignee_id:
        e.add_field(name="Исполнитель", value=f"<@{assignee_id}>")
    msg = await ch.send(embed=e, view=TodoView(), allowed_mentions=discord.AllowedMentions(users=[discord.Object(assignee_id)] if assignee_id else []))
    await db.run(db.execute, "UPDATE todo_tasks SET message_id=? WHERE id=?", (msg.id, tid))
    return f"Задача #{tid} создана"


async def publish_changelog(guild, cfg, title: str, text: str, author: str) -> str:
    ch = guild.get_channel(cfg["channel"]) if cfg["channel"] else None
    if ch is None:
        raise modules.ValidationError("Сначала выберите канал изменений и сохраните")
    e = embeds.make(title, text, color=parse_color(cfg["color"]))
    if (cfg["thumbnail"] or "").startswith("https://"):
        e.set_thumbnail(url=cfg["thumbnail"])
    role = guild.get_role(cfg["ping_role"]) if cfg["ping_role"] else None
    await ch.send(role.mention if role else None, embed=e, allowed_mentions=discord.AllowedMentions(roles=[role] if role else []))
    await db.run(db.execute, "INSERT INTO changelog_entries (guild_id, title, body, author, ts) VALUES (?,?,?,?,?)",
                 (guild.id, title, text, author, int(time.time())))
    return f"Опубликовано в #{ch.name}"


# ── panel actions ───────────────────────────────────────────────────────────

async def _embed_send(ctx, p):
    ch = ctx.guild.get_channel(p["channel"])
    if ch is None or not hasattr(ch, "send"):
        raise modules.ValidationError("Выберите текстовый канал")
    if not (p.get("title") or p.get("description")):
        raise modules.ValidationError("Нужен хотя бы заголовок или описание")
    e = discord.Embed(title=p.get("title") or None, description=p.get("description") or None, color=parse_color(p.get("color")))
    for url_key, setter in (("image", e.set_image), ("thumbnail", e.set_thumbnail)):
        if (p.get(url_key) or "").startswith("https://"):
            setter(url=p[url_key])
    if p.get("footer"):
        e.set_footer(text=p["footer"])
    await ch.send(p.get("content") or None, embed=e, allowed_mentions=discord.AllowedMentions.none())
    return f"Отправлено в #{ch.name}"


EMBED_CREATOR.actions.append(modules.Action("send", "Создать и отправить эмбед", _embed_send, params=[
    {"key": "channel", "label": "Канал", "type": "channel", "kind": "text"},
    {"key": "content", "label": "Текст над эмбедом", "type": "text", "required": False},
    {"key": "title", "label": "Заголовок", "type": "text", "required": False},
    {"key": "description", "label": "Описание", "type": "longtext", "required": False},
    {"key": "color", "label": "Цвет (HEX)", "type": "text", "default": "#BE1FB6", "required": False},
    {"key": "image", "label": "Картинка (https://…)", "type": "text", "required": False},
    {"key": "thumbnail", "label": "Миниатюра (https://…)", "type": "text", "required": False},
    {"key": "footer", "label": "Подвал", "type": "text", "required": False}]))


async def _changelog_publish(ctx, p):
    return await publish_changelog(ctx.guild, modules.get_config(ctx.guild.id, "changelog"), p["title"], p["text"], ctx.user_name or "панель")


CHANGELOG.actions.append(modules.Action("publish", "Опубликовать изменения", _changelog_publish, params=[
    {"key": "title", "label": "Заголовок", "type": "text"}, {"key": "text", "label": "Что изменилось", "type": "longtext"}]))


async def _news_panel(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "newsletter")
    ch = ctx.guild.get_channel(cfg["channel"]) if cfg["channel"] else None
    if ch is None:
        raise modules.ValidationError("Сначала выберите канал подписки и сохраните")
    await ch.send(embed=embeds.make(cfg["panel_title"], cfg["panel_text"], color=parse_color(cfg["color"])), view=NewsView())
    return f"Панель подписки отправлена в #{ch.name}"


async def _news_send(ctx, p):
    import asyncio
    cfg = modules.get_config(ctx.guild.id, "newsletter")
    subs = await db.run(db.query, "SELECT user_id FROM news_subs WHERE guild_id=?", (ctx.guild.id,))
    e = embeds.make(p["title"], p["text"], color=parse_color(cfg["color"]))
    e.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    sent = failed = 0
    for r in subs:
        member = ctx.guild.get_member(r["user_id"])
        if member is None:
            continue
        try:
            await member.send(embed=e)
            sent += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
        await asyncio.sleep(0.6)
    await db.run(db.execute, "INSERT INTO news_history (guild_id, title, body, sent, failed, author, ts) VALUES (?,?,?,?,?,?,?)",
                 (ctx.guild.id, p["title"], p["text"], sent, failed, ctx.user_name, int(time.time())))
    return f"Доставлено: {sent}, не дошло: {failed}"


NEWSLETTER.actions.extend([
    modules.Action("panel", "Отправить панель подписки", _news_panel, description="Сообщение с кнопкой в канал подписки"),
    modules.Action("send", "Разослать подписчикам", _news_send, confirm="Рассылка уйдёт всем подписчикам в личные сообщения.", params=[
        {"key": "title", "label": "Заголовок", "type": "text"}, {"key": "text", "label": "Сообщение", "type": "longtext"}]),
])


async def _todo_add(ctx, p):
    return await create_task(ctx.guild, modules.get_config(ctx.guild.id, "todo"), p["text"], p.get("assignee"), ctx.user_id)


async def _todo_delete(ctx, p):
    rows = await db.run(db.query, "SELECT * FROM todo_tasks WHERE id=? AND guild_id=?", (int(p["id"]), ctx.guild.id))
    if not rows:
        raise modules.ValidationError("Задачи с таким номером нет")
    t = rows[0]
    ch = ctx.guild.get_channel(t["channel_id"]) if t["channel_id"] else None
    if ch is not None and t["message_id"]:
        try:
            await ch.get_partial_message(t["message_id"]).delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
    await db.run(db.execute, "DELETE FROM todo_tasks WHERE id=?", (t["id"],))
    return "Задача удалена"


TODO.actions.extend([
    modules.Action("add", "Создать задачу", _todo_add, params=[
        {"key": "text", "label": "Что нужно сделать", "type": "longtext"},
        {"key": "assignee", "label": "Исполнитель", "type": "user", "required": False}]),
    modules.Action("delete", "Удалить задачу", _todo_delete, danger=True, params=[{"key": "id", "label": "Номер задачи", "type": "number", "min": 1}]),
])


async def _partner_add(ctx, p):
    await db.run(db.execute, "INSERT INTO partners (guild_id, name, invite, rep, note, ts) VALUES (?,?,?,?,?,?)",
                 (ctx.guild.id, p["name"], p.get("invite") or "", p.get("rep") or "", p.get("note") or "", int(time.time())))
    return "Партнёр добавлен"


async def _partner_remove(ctx, p):
    cur = await db.run(db.execute, "DELETE FROM partners WHERE id=? AND guild_id=?", (int(p["id"]), ctx.guild.id))
    if not cur.rowcount:
        raise modules.ValidationError("Партнёра с таким номером нет")
    return "Партнёр удалён"


async def _partner_publish(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "partnership")
    ch = ctx.guild.get_channel(cfg["partners_channel"]) if cfg["partners_channel"] else None
    if ch is None:
        raise modules.ValidationError("Сначала выберите канал партнёров и сохраните")
    rows = await db.run(db.query, "SELECT * FROM partners WHERE guild_id=? ORDER BY id", (ctx.guild.id,))
    lines = [f"**{r['name']}**" + (f" — {r['invite']}" if r["invite"] else "") + (f"\n> {r['note']}" if r["note"] else "") for r in rows]
    e = embeds.make(cfg["title"], (cfg["description"] + "\n\n" if cfg["description"] else "") + ("\n\n".join(lines) or "Пока партнёров нет."), color=COLOR)
    await ch.send(embed=e)
    return f"Список опубликован в #{ch.name}"


PARTNERSHIP.actions.extend([
    modules.Action("add", "Добавить партнёра", _partner_add, params=[
        {"key": "name", "label": "Название сервера", "type": "text"},
        {"key": "invite", "label": "Приглашение", "type": "text", "required": False},
        {"key": "rep", "label": "Представитель", "type": "text", "required": False},
        {"key": "note", "label": "Заметка", "type": "text", "required": False}]),
    modules.Action("remove", "Удалить партнёра", _partner_remove, danger=True, params=[{"key": "id", "label": "Номер", "type": "number", "min": 1}]),
    modules.Action("publish", "Опубликовать список", _partner_publish),
])


async def _give(ctx, p):
    cfg = modules.get_config(ctx.guild.id, "minigames")
    await db.run(wallet, ctx.guild.id, int(p["user"]), cfg["start_balance"])
    await db.run(add_balance, ctx.guild.id, int(p["user"]), int(p["amount"]))
    return f"Баланс изменён на {int(p['amount'])}"


MINIGAMES.actions.append(modules.Action("give", "Выдать или снять баланс", _give, params=[
    {"key": "user", "label": "Участник", "type": "user"},
    {"key": "amount", "label": "Сколько (отрицательное — снять)", "type": "number", "min": -10000000, "max": 10000000}]))


# ── tables ──────────────────────────────────────────────────────────────────

def _name(guild, uid):
    m = guild.get_member(uid)
    return m.display_name if m else str(uid)


modules.register_table("changelog", "history", lambda g: [dict(r) for r in db.query(
    "SELECT title, author, ts FROM changelog_entries WHERE guild_id=? ORDER BY id DESC LIMIT 50", (g.id,))])
modules.register_table("newsletter", "history", lambda g: [dict(r) for r in db.query(
    "SELECT title, sent, failed, ts FROM news_history WHERE guild_id=? ORDER BY id DESC LIMIT 50", (g.id,))])
modules.register_table("todo", "tasks", lambda g: [
    {"id": r["id"], "text": r["text"][:120], "assignee": _name(g, r["assignee_id"]) if r["assignee_id"] else "—",
     "status": "выполнена" if r["done"] else "в работе", "ts": r["ts"]}
    for r in db.query("SELECT * FROM todo_tasks WHERE guild_id=? ORDER BY id DESC LIMIT 100", (g.id,))])
modules.register_table("partnership", "list", lambda g: [dict(r) for r in db.query(
    "SELECT id, name, invite, rep, ts FROM partners WHERE guild_id=? ORDER BY id", (g.id,))])
modules.register_table("voucher", "recent", lambda g: [
    {"target": _name(g, r["target_id"]), "voter": _name(g, r["voter_id"]), "verdict": "честный" if r["positive"] else "скам",
     "comment": r["comment"], "ts": r["ts"]}
    for r in db.query("SELECT * FROM vouches WHERE guild_id=? ORDER BY ts DESC LIMIT 100", (g.id,))])
modules.register_table("minigames", "top", lambda g: [
    {"user": _name(g, r["user_id"]), "balance": r["balance"]}
    for r in db.query("SELECT user_id, balance FROM mg_wallet WHERE guild_id=? ORDER BY balance DESC LIMIT 20", (g.id,))])


async def setup(bot):
    await bot.add_cog(CommunityTools(bot))
