"""timers -- split out of main.py."""

import asyncio
import os
from datetime import datetime, timedelta

import discord
from discord.ext import tasks
from legacy.app import bot, now_msk
from legacy.state import (
    backup_settings,
    event_lists,
    save_data,
    voice_presence_settings,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


async def send_backup_now(guild: discord.Guild) -> bool:
    """Собирает выбранные файлы и шлёт их в настроенный канал бэкапов. True при успехе."""
    bs = backup_settings.get(guild.id, {})
    channel_id = bs.get("channel_id")
    filenames  = bs.get("files", [])
    if not channel_id or not filenames:
        return False
    channel = guild.get_channel(channel_id) or bot.get_channel(channel_id)
    if channel is None:
        return False

    save_data()  # актуализировать data.json перед отправкой

    files = []
    missing = []
    for fn in filenames:
        path = os.path.join(BASE_DIR, fn)
        if os.path.exists(path):
            files.append(path)
        else:
            missing.append(fn)

    ts = now_msk().strftime("%d.%m.%Y %H:%M")
    sent_any = False
    try:
        for i in range(0, len(files), 10):
            chunk = files[i:i + 10]
            await channel.send(
                content=f"💾 Автобэкап · {ts} (МСК)" if i == 0 else None,
                files=[discord.File(p, filename=os.path.basename(p)) for p in chunk],
            )
            sent_any = True
        if missing:
            await channel.send(f"⚠️ Не найдены файлы: {', '.join(missing)}")
    except discord.HTTPException as e:
        print(f"WARNING: backup send failed for guild {guild.id}: {e}")
        return False

    if sent_any:
        bs["last_backup"] = ts
        save_data()
    return sent_any


@tasks.loop(minutes=15)
async def backup_scheduler_loop():
    """Раз в 15 минут проверяет, для каких серверов настало время автобэкапа."""
    now = now_msk()
    for guild_id, bs in list(backup_settings.items()):
        if not bs.get("channel_id") or not bs.get("files"):
            continue
        interval = bs.get("interval_hours", 1)
        last = bs.get("last_backup")
        try:
            last_dt = datetime.strptime(last, "%d.%m.%Y %H:%M") if last else None
        except ValueError:
            last_dt = None
        if last_dt and now - last_dt < timedelta(hours=interval):
            continue
        guild = bot.get_guild(guild_id)
        if guild:
            try:
                ok = await send_backup_now(guild)
                if not ok:
                    print(f"WARNING: backup_scheduler_loop guild={guild_id}: send_backup_now returned False")
                    channel = guild.get_channel(bs["channel_id"]) or bot.get_channel(bs["channel_id"])
                    if channel:
                        try:
                            await channel.send("⚠️ Автобэкап не отправлен: проверь права бота в этом канале или список файлов.")
                        except Exception:
                            pass
            except Exception as e:
                print(f"WARNING: backup_scheduler_loop guild={guild_id}: {e}")
                guild_after = bot.get_guild(guild_id)
                if guild_after:
                    channel = guild_after.get_channel(bs["channel_id"]) or bot.get_channel(bs["channel_id"])
                    if channel:
                        try:
                            await channel.send(f"⚠️ Автобэкап упал с ошибкой: {e}")
                        except Exception:
                            pass


@tasks.loop(minutes=1)
async def vzh_reminder_loop():
    """Каждую минуту проверяет сборы !vzh и за 30 минут до времени шлёт ЛС-напоминание тем, кто занял слот."""
    import re as _re
    now_msk_dt = now_msk()
    for msg_id, ev in list(event_lists.items()):
        if ev.get("cmd") != "vzh" or ev.get("closed") or ev.get("reminded"):
            continue
        try:
            event_dt_str = ev.get("event_datetime")
            if event_dt_str:
                # Точная дата/время, посчитанные при создании сбора по расписанию ВЗХ фракции
                target = datetime.fromisoformat(event_dt_str)
            else:
                # Старые сборы (созданы до появления event_datetime) — прежняя эвристика "сегодня/завтра"
                m = _re.match(r"^(\d{1,2}):(\d{2})$", (ev.get("event_time") or "").strip())
                if not m:
                    continue
                hour, minute = int(m.group(1)), int(m.group(2))
                created_at = ev.get("created_at")
                anchor = datetime.fromisoformat(created_at) if created_at else now_msk_dt
                try:
                    target = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
                except ValueError:
                    continue
                if target < anchor:
                    target += timedelta(days=1)
            remind_at = target - timedelta(minutes=30)
            if not (remind_at <= now_msk_dt < target):
                continue

            recipients = [uid for uid in ev.get("slots", {}).values() if uid]
            for uid in recipients:
                try:
                    user = await bot.fetch_user(uid)
                    embed = discord.Embed(
                        title="⏰ Напоминание о сборе ВЗХ",
                        description=(
                            f"**{ev.get('title', 'ВЗХ')}**\n"
                            f"Начало в `{ev.get('event_time')}` — через 30 минут!"
                        ),
                        color=discord.Color.orange(),
                    )
                    await user.send(embed=embed)
                except Exception as e:
                    print(f"WARNING: vzh_reminder_loop DM {uid}: {e}")

            ev["reminded"] = True
            save_data()
        except Exception as e:
            print(f"WARNING: vzh_reminder_loop event {msg_id}: {e}")


@vzh_reminder_loop.error
async def vzh_reminder_loop_error(error: Exception):
    print(f"WARNING: vzh_reminder_loop crashed, restarting: {error}")
    if not vzh_reminder_loop.is_running():
        vzh_reminder_loop.start()


_voice_presence_locks: dict = {}  # { guild_id: asyncio.Lock }


async def _voice_presence_ensure(guild: discord.Guild):
    """Подключает бота к настроенному голосовому каналу, если он должен там быть.

    reconnect=False специально: встроенный авто-реконнект discord.py при обрыве
    хендшейка (код 4006 — "сессия недействительна") зацикливается на уже протухшей
    сессии и ретраит бесконечно внутри самого connect(). Здесь один неудачный
    коннект просто логируется, а свежую попытку (с новой сессией) делает
    voice_presence_loop раз в 5 минут или следующий вызов из on_voice_state_update.
    """
    vp = voice_presence_settings.get(guild.id)
    if not vp or not vp.get("enabled") or not vp.get("channel_id"):
        return
    channel = guild.get_channel(vp["channel_id"])
    if not channel or not isinstance(channel, discord.VoiceChannel):
        return

    lock = _voice_presence_locks.setdefault(guild.id, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        vc = guild.voice_client
        try:
            if vc and vc.is_connected():
                if vc.channel.id != channel.id:
                    await vc.move_to(channel)
                return
            if vc:
                # Оставшийся "мёртвый" клиент от неудавшегося коннекта — сбросить перед новой попыткой
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            await channel.connect(self_mute=True, self_deaf=True, reconnect=False, timeout=15)
        except Exception as e:
            print(f"WARNING: voice_presence_ensure guild={guild.id}: {e}")


@tasks.loop(minutes=5)
async def voice_presence_loop():
    """Раз в 5 минут проверяет, что бот сидит в настроенном войс-канале, и переподключает при необходимости."""
    for guild_id, vp in list(voice_presence_settings.items()):
        if not vp.get("enabled") or not vp.get("channel_id"):
            continue
        guild = bot.get_guild(guild_id)
        if guild:
            try:
                await _voice_presence_ensure(guild)
            except Exception as e:
                print(f"WARNING: voice_presence_loop guild={guild_id}: {e}")


@voice_presence_loop.error
async def voice_presence_loop_error(error: Exception):
    print(f"WARNING: voice_presence_loop crashed, restarting: {error}")
    if not voice_presence_loop.is_running():
        voice_presence_loop.start()
