"""vzp_monitor -- split out of main.py."""

import re
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands, ui
from discord.ext import tasks
from legacy.app import bot, tree
from legacy.helpers import (
    is_admin,
)
from legacy.state import (
    save_data,
    vzp_last_check,
    vzp_monitor_config,
    vzp_processed_events,
)


VZP_API = "https://vzp-gta5rp.com/api"


async def _vzp_get(session: aiohttp.ClientSession, path: str, **params):
    try:
        async with session.get(
            f"{VZP_API}{path}", params=params or None,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0 (compatible; bot/1.0)"},
        ) as r:
            if r.status == 200:
                return await r.json(content_type=None)
            body = await r.text()
            print(f"VZP API error {path}: HTTP {r.status} — {body[:200]}")
    except Exception as e:
        print(f"VZP API error {path}: {type(e).__name__}: {e}")
    return None


def _vzp_unwrap(data):
    """Разворачивает обёртку {"data": [...]} если есть."""
    if isinstance(data, dict):
        for key in ("data", "items", "history", "organizations"):
            if key in data:
                return data[key]
    return data


def _vzp_mentions(guild_id: int) -> str:
    cfg = vzp_monitor_config.get(guild_id, {})
    parts = [f"<@&{r}>" for r in cfg.get("mentionRoles", [])]
    parts += [f"<@{u}>" for u in cfg.get("mentionUsers", [])]
    return " ".join(parts)


def _parse_vzp_ts(raw) -> int | None:
    """API отдаёт ISO8601 ('2026-07-28T17:57:05.000Z'), а не unix-время."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    try:
        return int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _duration_str(start_ts, end_ts) -> str:
    start = _parse_vzp_ts(start_ts)
    end = _parse_vzp_ts(end_ts)
    if start is None or end is None:
        return "—"
    secs = end - start
    m, s = divmod(abs(secs), 60)
    return f"{m}м {s}с"


# Внутренние коды точек ВЗП → читаемое название района (собрано по /stats/organizations/{id}/history)
VZP_DISTRICT_NAMES = {
    "STABCITY":                 "Байкерка",
    "EL_RANCHO_SMALL_OILBASE":  "Малая нефть",
    "BANNING_ANGAR":            "Мясо",
    "SANDYSHORES":              "Сенди Шорс",
    "GHETTO_ANTS":              "Муравейник",
    "NICOLA_PLACE":             "Тупик Миррор",
    "PUERTA_DUMP":              "Мусорка",
    "ELBURRO":                  "Татушка",
    "WINDFARM":                 "Ветряки",
    "PB_LUMBER":                "Лесопилка",
    "PALETOBAY":                "Палето Бей",
    "MIRROR_PARK":              "Миррор Парк",
}


def _vzp_map_name(event: dict) -> str:
    """'NEW_S_STABCITY' + pointName 'White Water AC' → 'White Water AC — Байкерка'."""
    point = event.get("pointName") or "?"
    code = event.get("map") or ""
    key = re.sub(r"^NEW_[SB]_", "", code)
    district = VZP_DISTRICT_NAMES.get(key)
    return f"{point} — {district}" if district else point


def _player_table(players: list) -> str:
    if not players:
        return "```нет данных```"
    header = f"{'Игрок':<20} {'K':>4} {'DMG':>7} {'HIT%':>5} {'HS%':>5}"
    sep = "─" * len(header)
    rows = [header, sep]
    for p in sorted(players, key=lambda x: x.get("kills", 0), reverse=True):
        name = str(p.get("charName") or p.get("characterName") or p.get("name") or "?")[:20]
        k    = p.get("kills", 0)
        dmg  = p.get("damage", 0)
        hit  = f"{p.get('hitPercent', 0):.1f}"
        hs   = f"{p.get('hsPercent', 0):.1f}"
        rows.append(f"{name:<20} {k:>4} {dmg:>7} {hit:>5} {hs:>5}")
    return "```\n" + "\n".join(rows) + "\n```"


async def _send_war_started(guild_id: int, event: dict):
    cfg = vzp_monitor_config.get(guild_id)
    if not cfg:
        return
    ch = bot.get_channel(cfg["alertChannelId"])
    if not ch:
        return

    family_name = cfg["familyName"]
    atk_name = event.get("attackerName", "?")
    def_name = event.get("defenderName", "?")
    our_side = "ATK ⚔️" if atk_name == family_name else "DEF 🛡️"
    opponent = def_name if atk_name == family_name else atk_name

    ts = _parse_vzp_ts(event.get("startedAt"))
    ts_str = f"<t:{ts}:T>" if ts else "—"

    embed = discord.Embed(
        title=f"⚔️ ВОЙНА НАЧАЛАСЬ — {event.get('pointName', '?')}",
        color=0xFF8C00,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Наша роль",     value=our_side,                        inline=True)
    embed.add_field(name="Противник",     value=opponent,                        inline=True)
    embed.add_field(name="Карта",         value=_vzp_map_name(event),            inline=True)
    embed.add_field(name="Макс. игроков", value=str(event.get("maxPlayers","?")),inline=True)
    embed.add_field(name="Начало",        value=ts_str,                          inline=True)
    embed.set_footer(text="vzp-gta5rp.com")

    mention = _vzp_mentions(guild_id)
    await ch.send(content=mention or None, embed=embed)


async def _send_war_result(guild_id: int, event: dict):
    cfg = vzp_monitor_config.get(guild_id)
    if not cfg:
        return
    ch = bot.get_channel(cfg["resultsChannelId"])
    if not ch:
        return

    family_name = cfg["familyName"]
    atk_name    = event.get("attackerName", "?")
    def_name    = event.get("defenderName", "?")
    winner_name = event.get("winnerName")
    we_are_atk  = (atk_name == family_name)

    we_won = (winner_name == family_name)
    color  = 0x57F287 if we_won else 0xED4245
    title  = ("✅ ПОБЕДА" if we_won else "❌ ПОРАЖЕНИЕ") + f" — {event.get('pointName','?')}"

    start_ts = event.get("startedAt")
    end_ts   = event.get("endedAt")
    duration = _duration_str(start_ts, end_ts)
    start_epoch = _parse_vzp_ts(start_ts)
    end_epoch   = _parse_vzp_ts(end_ts)
    start_str = f"<t:{start_epoch}:t>" if start_epoch else "—"
    end_str   = f"<t:{end_epoch}:t>"   if end_epoch   else "—"

    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Атака",      value=atk_name,               inline=True)
    embed.add_field(name="Защита",     value=def_name,               inline=True)
    embed.add_field(name="Победитель", value=winner_name or "?",     inline=True)
    embed.add_field(name="Карта",      value=_vzp_map_name(event),   inline=True)
    embed.add_field(name="Длительность", value=duration,             inline=True)

    atk_players = event.get("attackers") or []
    def_players = event.get("defenders") or []
    atk_stats = event.get("attackerStats") or {}
    def_stats = event.get("defenderStats") or {}

    our_players,  our_stats  = (atk_players, atk_stats) if we_are_atk else (def_players, def_stats)
    enemy_players, enemy_stats = (def_players, def_stats) if we_are_atk else (atk_players, atk_stats)

    our_label = f"Наша команда ({'ATK' if we_are_atk else 'DEF'})"
    embed.add_field(
        name=our_label,
        value=f"K: **{our_stats.get('kills',0)}** | DMG: **{our_stats.get('damage',0)}** | HS: **{our_stats.get('headshots',0)}**",
        inline=False,
    )
    embed.add_field(
        name="Противник",
        value=f"K: **{enemy_stats.get('kills',0)}** | DMG: **{enemy_stats.get('damage',0)}** | HS: **{enemy_stats.get('headshots',0)}**",
        inline=False,
    )
    if our_players:
        embed.add_field(name=f"📊 {our_label}", value=_player_table(our_players), inline=False)
    if enemy_players:
        embed.add_field(name="📊 Противник",    value=_player_table(enemy_players), inline=False)

    embed.set_footer(text=f"⏱ {start_str} → {end_str} | vzp-gta5rp.com")
    await ch.send(embed=embed)


@tasks.loop(seconds=15)
async def vzp_monitor_loop():
    import time
    now = time.time()
    async with aiohttp.ClientSession() as session:
        for guild_id, cfg in list(vzp_monitor_config.items()):
            if not cfg.get("monitoringEnabled"):
                continue
            interval = cfg.get("pollInterval", 20)
            if now - vzp_last_check.get(guild_id, 0) < interval:
                continue
            vzp_last_check[guild_id] = now

            family_name = cfg.get("familyName")
            server_id = cfg.get("serverId")
            if not family_name or not server_id:
                continue

            processed = vzp_processed_events.setdefault(guild_id, {})
            changed = False

            # API игнорирует параметр serverId — фильтруем сами, ниже.
            events = await _vzp_get(session, "/events", limit=100)
            events = _vzp_unwrap(events)
            if not isinstance(events, list):
                events = []

            for ev in events:
                eid = str(ev.get("id") or ev.get("eventId") or "")
                if not eid:
                    continue
                if ev.get("serverId") != server_id:
                    continue
                if ev.get("attackerName") != family_name and ev.get("defenderName") != family_name:
                    continue

                ended  = ev.get("endedAt")
                status = processed.get(eid)

                if ended is None and status is None:
                    processed[eid] = "notified"
                    changed = True
                    try:
                        await _send_war_started(guild_id, ev)
                    except Exception as e:
                        print(f"VZP send_started error: {e}")

                elif ended is not None and status == "notified":
                    detail = await _vzp_get(session, f"/events/{eid}")
                    detail = _vzp_unwrap(detail) or ev
                    processed[eid] = "completed"
                    changed = True
                    try:
                        await _send_war_result(guild_id, detail)
                    except Exception as e:
                        print(f"VZP send_result error: {e}")

                elif ended is not None and status is None:
                    # bot missed the start — still send result notification
                    detail = await _vzp_get(session, f"/events/{eid}")
                    detail = _vzp_unwrap(detail) or ev
                    processed[eid] = "completed"
                    changed = True
                    try:
                        await _send_war_result(guild_id, detail)
                    except Exception as e:
                        print(f"VZP send_result (missed start) error: {e}")

            if changed:
                save_data()


@vzp_monitor_loop.before_loop
async def _before_vzp():
    await bot.wait_until_ready()


def _parse_channel_id(val: str):
    cleaned = val.strip().lstrip("<#").rstrip(">")
    try:
        return int(cleaned)
    except ValueError:
        return None


class VzpSetupModal(ui.Modal, title="⚔️ Настройка мониторинга ВЗП"):
    family_input = ui.TextInput(
        label="ID семьи (из ссылки на vzp-gta5rp.com)",
        placeholder="15607 или https://vzp-gta5rp.com/stats/families/15607",
        required=True,
        max_length=200,
    )
    server_input = ui.TextInput(
        label="ID сервера GTA5RP",
        placeholder="1=Downtown, 20=Murrieta, 4=Vinewood, 5=Rockford",
        required=True,
        max_length=5,
    )
    alert_input = ui.TextInput(
        label="Канал уведомлений о начале войны",
        placeholder="ID канала или #упоминание",
        required=True,
        max_length=30,
    )
    results_input = ui.TextInput(
        label="Канал результатов войны",
        placeholder="ID канала или #упоминание",
        required=True,
        max_length=30,
    )
    mentions_input = ui.TextInput(
        label="Роли/юзеры для тега (ID через запятую)",
        placeholder="123456789, 987654321",
        required=False,
        max_length=300,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.family_input.value.strip().rstrip("/")
        family_id_str = raw.split("/")[-1]
        try:
            family_id = int(family_id_str)
        except ValueError:
            await interaction.response.send_message(
                "❌ Введи числовой ID семьи или ссылку вида `vzp-gta5rp.com/stats/families/15607`.", ephemeral=True
            )
            return

        async with aiohttp.ClientSession() as session:
            org_raw = await _vzp_get(session, f"/stats/organizations/{family_id}")
        org = _vzp_unwrap(org_raw)
        if not isinstance(org, dict) or not org.get("id"):
            await interaction.response.send_message(
                f"❌ Семья с ID `{family_id}` не найдена в API.", ephemeral=True
            )
            return

        await self._apply(interaction, org)

    async def _apply(self, interaction: discord.Interaction, family: dict):
        guild_id = interaction.guild_id

        try:
            srv_id = int(self.server_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ ID сервера должен быть числом.", ephemeral=True)
            return

        alert_ch   = _parse_channel_id(self.alert_input.value)
        results_ch = _parse_channel_id(self.results_input.value)
        if not alert_ch or not results_ch:
            await interaction.response.send_message("❌ Не удалось распознать ID каналов.", ephemeral=True)
            return

        mention_roles, mention_users = [], []
        for part in (self.mentions_input.value or "").split(","):
            part = part.strip().lstrip("<@&># ").rstrip("> ")
            if not part:
                continue
            try:
                mid = int(part)
                if interaction.guild.get_role(mid):
                    mention_roles.append(mid)
                else:
                    mention_users.append(mid)
            except ValueError:
                pass

        vzp_monitor_config[guild_id] = {
            "familyId":         family.get("id"),
            "familyName":       family.get("name"),
            "serverId":         srv_id,
            "alertChannelId":   alert_ch,
            "resultsChannelId": results_ch,
            "mentionRoles":     mention_roles,
            "mentionUsers":     mention_users,
            "pollInterval":     20,
            "monitoringEnabled": True,
        }
        vzp_processed_events.setdefault(guild_id, {})
        save_data()

        embed = discord.Embed(title="✅ Мониторинг ВЗП настроен", color=0x57F287, timestamp=datetime.now())
        embed.add_field(name="Семья",              value=f"{family.get('name')} (ID: {family.get('id')})", inline=False)
        embed.add_field(name="Сервер GTA5RP",      value=str(srv_id),         inline=True)
        embed.add_field(name="Канал начала войны", value=f"<#{alert_ch}>",    inline=True)
        embed.add_field(name="Канал результатов",  value=f"<#{results_ch}>",  inline=True)
        embed.add_field(name="Теги ролей",  value=" ".join(f"<@&{r}>" for r in mention_roles)  or "нет", inline=True)
        embed.add_field(name="Теги юзеров", value=" ".join(f"<@{u}>"  for u in mention_users)  or "нет", inline=True)
        embed.add_field(name="Интервал",    value="20 сек", inline=True)
        embed.set_footer(text="Мониторинг запущен")
        try:
            await interaction.response.send_message(embed=embed)
        except discord.InteractionResponded:
            await interaction.followup.send(embed=embed)


@tree.command(name="взп-настройка", description="Настроить мониторинг войн ВЗП")
async def vzp_setup_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    # Показываем список серверов перед модалом
    async with aiohttp.ClientSession() as session:
        servers = await _vzp_get(session, "/servers")
    servers = _vzp_unwrap(servers)
    modal = VzpSetupModal()
    if isinstance(servers, list) and servers:
        placeholder = ", ".join(f"{s.get('id')}={s.get('name','?')}" for s in servers[:8])
        modal.server_input.placeholder = placeholder[:100]
    await interaction.response.send_modal(modal)


@tree.command(name="взп-статус", description="Текущие настройки мониторинга ВЗП")
async def vzp_status_cmd(interaction: discord.Interaction):
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен. Используй `/взп-настройка`.", ephemeral=True)
        return
    embed = discord.Embed(title="⚙️ Статус мониторинга ВЗП", color=0x5865F2, timestamp=datetime.now())
    embed.add_field(name="Семья",    value=f"{cfg.get('familyName')} (ID: {cfg.get('familyId')})", inline=False)
    embed.add_field(name="Сервер",   value=str(cfg.get("serverId")),   inline=True)
    embed.add_field(name="Статус",   value="🟢 Включён" if cfg.get("monitoringEnabled") else "🔴 Остановлен", inline=True)
    embed.add_field(name="Интервал", value=f"{cfg.get('pollInterval', 20)} сек", inline=True)
    embed.add_field(name="Канал начала",     value=f"<#{cfg.get('alertChannelId')}>",   inline=True)
    embed.add_field(name="Канал результатов",value=f"<#{cfg.get('resultsChannelId')}>", inline=True)
    embed.add_field(name="Теги ролей",  value=" ".join(f"<@&{r}>" for r in cfg.get("mentionRoles",[]))  or "нет", inline=True)
    embed.add_field(name="Теги юзеров", value=" ".join(f"<@{u}>"  for u in cfg.get("mentionUsers",[]))  or "нет", inline=True)
    embed.add_field(name="Обработано войн", value=str(len(vzp_processed_events.get(interaction.guild_id, {}))), inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="взп-стоп", description="Остановить мониторинг ВЗП")
async def vzp_stop_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    cfg["monitoringEnabled"] = False
    save_data()
    await interaction.response.send_message("🔴 Мониторинг ВЗП остановлен.")


@tree.command(name="взп-старт", description="Запустить мониторинг ВЗП")
async def vzp_start_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен. Используй `/взп-настройка`.", ephemeral=True)
        return
    cfg["monitoringEnabled"] = True
    save_data()
    await interaction.response.send_message("🟢 Мониторинг ВЗП запущен.")


@tree.command(name="взп-интервал", description="Изменить интервал опроса API (15–120 сек)")
@app_commands.describe(секунды="Интервал опроса в секундах (мин. 15, макс. 120)")
async def vzp_interval_cmd(interaction: discord.Interaction, секунды: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    if not (15 <= секунды <= 120):
        await interaction.response.send_message("❌ Интервал: от 15 до 120 секунд.", ephemeral=True)
        return
    cfg["pollInterval"] = секунды
    save_data()
    await interaction.response.send_message(f"✅ Интервал опроса: **{секунды} сек**.")


@tree.command(name="взп-семья", description="Статистика семьи на vzp-gta5rp.com")
async def vzp_family_cmd(interaction: discord.Interaction):
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        raw = await _vzp_get(session, f"/stats/organizations/{cfg['familyId']}")
    data = _vzp_unwrap(raw) if raw else None
    if not data or not isinstance(data, dict):
        await interaction.followup.send("❌ Не удалось получить статистику семьи.")
        return

    embed = discord.Embed(
        title=f"📊 Статистика — {data.get('name', cfg['familyName'])}",
        color=0x5865F2,
        timestamp=datetime.now(),
    )
    embed.add_field(name="Побед",      value=str(data.get("wins",   "?")), inline=True)
    embed.add_field(name="Поражений",  value=str(data.get("losses", "?")), inline=True)
    wr = data.get("winrate") or data.get("winRate")
    if wr is not None:
        embed.add_field(name="Винрейт", value=f"{float(wr):.1f}%", inline=True)
    gr = data.get("rankGlobal") or data.get("rank") or data.get("globalRank") or data.get("position")
    embed.add_field(name="Глобальный ранк", value=f"#{gr}" if gr is not None else "?", inline=True)
    sr = data.get("rankServer") or data.get("serverRank")
    if sr:
        embed.add_field(name="Ранк на сервере", value=f"#{sr}", inline=True)
    embed.set_footer(text="vzp-gta5rp.com")
    await interaction.followup.send(embed=embed)


@tree.command(name="взп-история", description="История последних войн семьи")
@app_commands.describe(количество="Сколько войн показать (1–20, по умолчанию 5)")
async def vzp_history_cmd(interaction: discord.Interaction, количество: int = 5):
    cfg = vzp_monitor_config.get(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Мониторинг не настроен.", ephemeral=True)
        return
    количество = max(1, min(количество, 20))
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        raw = await _vzp_get(session, f"/stats/organizations/{cfg['familyId']}/history",
                             limit=количество, offset=0)
    items = _vzp_unwrap(raw)
    if not isinstance(items, list):
        items = []

    embed = discord.Embed(
        title=f"📜 История войн — {cfg['familyName']}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    for ev in items[:количество]:
        role     = ev.get("role", "?")
        opponent = ev.get("opponentName", "?")
        result   = "✅ Победа" if ev.get("isWin") else "❌ Поражение"
        map_name = ev.get("map", "?")
        ts       = _parse_vzp_ts(ev.get("date"))
        time_s   = f"<t:{ts}:d>" if ts else "—"
        embed.add_field(
            name=f"{result} — {role}",
            value=f"vs {opponent}\n📍 {map_name}\n{time_s}",
            inline=False,
        )
    if not items:
        embed.description = "История войн пуста."
    embed.set_footer(text="vzp-gta5rp.com")
    await interaction.followup.send(embed=embed)
