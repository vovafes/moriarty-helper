"""events -- split out of main.py."""

import io
import re

import discord
from discord import app_commands, ui
from legacy.app import bot, now_msk, tree
from legacy.helpers import (
    _footer,
    build_event_embed,
    can_manage_event_message,
    can_run_event,
    get_vzh_faction,
    is_admin,
    is_admin_ctx,
    next_vzh_datetime,
)
from legacy.state import (
    event_command_roles,
    event_lists,
    event_roles,
    list_roles2,
    mp_roles,
    mp_roles2,
    save_data,
    vzh_roles,
    vzh_roles2,
    vzp_roles,
    vzp_roles2,
)


def build_thread_list(title: str, max_count: int, slots: dict, reserve: list | None = None) -> str:
    filled = sum(1 for v in slots.values() if v is not None)
    lines  = [f"**📋 Список: {title} ({filled}/{max_count})**\n"]
    for i in range(1, max_count + 1):
        uid = slots.get(i)
        lines.append(f"`{str(i).zfill(2)}.` {'<@' + str(uid) + '>' if uid else 'свободно'}")
    reserve = reserve or []
    lines.append(f"\n**🪑 Резерв ({len(reserve)}):**")
    if reserve:
        for i, uid in enumerate(reserve, 1):
            lines.append(f"`R{str(i).zfill(2)}.` <@{uid}>")
    else:
        lines.append("*пусто*")
    return "\n".join(lines)


async def update_thread_list(message_id: int):
    data = event_lists.get(message_id)
    if not data or not data.get("thread_msg_id"):
        return
    try:
        thread = bot.get_channel(data["thread_id"])
        if not thread:
            return
        msg = await thread.fetch_message(data["thread_msg_id"])
        await msg.edit(
            content=build_thread_list(data["title"], data["max"], data["slots"], data.get("reserve", [])),
            view=ThreadListView(message_id),
        )
    except Exception:
        pass


def _find_event_by_thread(thread_id: int):
    """Находит сбор по ID его треда с обсуждением."""
    for mid, data in event_lists.items():
        if data.get("thread_id") == thread_id:
            return mid, data
    return None, None


async def _handle_thread_slot_pick(message: discord.Message):
    """Альтернатива кнопкам: если написать номер слота в треде сбора — занимает/освобождает его."""
    content = message.content.strip()
    if not content.isdigit():
        return

    message_id, data = _find_event_by_thread(message.channel.id)
    if not data:
        return
    if data.get("closed"):
        return

    slot_num  = int(content)
    max_count = data.get("max", 0)
    if not (1 <= slot_num <= max_count):
        return

    user_id = message.author.id
    slots   = data["slots"]
    reserve = data.setdefault("reserve", [])

    if slots.get(slot_num) == user_id:
        slots[slot_num] = None
        reply = f"❌ {message.author.mention} покинул(а) слот **{slot_num}**"
    elif slots.get(slot_num) is not None:
        try:
            await message.channel.send(f"❌ Слот **{slot_num}** уже занят!", delete_after=6)
        except Exception:
            pass
        return
    else:
        for s, uid in list(slots.items()):
            if uid == user_id:
                slots[s] = None
        if user_id in reserve:
            reserve.remove(user_id)
        slots[slot_num] = user_id
        reply = f"✅ {message.author.mention} занял(а) слот **{slot_num}**!"

    save_data()
    join_mode = data.get("mode") == "join"
    try:
        channel  = bot.get_channel(data["channel_id"])
        orig_msg = await channel.fetch_message(message_id)
        embed = build_event_embed(
            message.guild.id, data["title"], data["max"], slots,
            data.get("image_url"), data.get("note"), join_mode=join_mode,
            event_time=data.get("event_time"), closed=data.get("closed", False),
            reserve=reserve,
        )
        view = JoinEventView(message_id) if join_mode else EventView(message_id)
        await orig_msg.edit(embed=embed, view=view)
    except Exception:
        pass

    await update_thread_list(message_id)
    try:
        await message.channel.send(reply, delete_after=6)
    except Exception:
        pass


class SlotButton(ui.Button):
    def __init__(self, slot_num: int, message_id: int, taken_by: int | None):
        super().__init__(
            label=str(slot_num),
            style=discord.ButtonStyle.danger if taken_by else discord.ButtonStyle.success,
            custom_id=f"slot_{message_id}_{slot_num}",
        )
        self.slot_num = slot_num
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор уже недоступен!", ephemeral=True)

        if data.get("closed"):
            return await interaction.response.send_message("🔒 Список закрыт!", ephemeral=True)

        user_id = interaction.user.id
        slots   = data["slots"]
        reserve = data.setdefault("reserve", [])

        if slots.get(self.slot_num) == user_id:
            # Покинуть слот
            slots[self.slot_num] = None
            msg_text = f"❌ Вы покинули слот **{self.slot_num}**"
        elif slots.get(self.slot_num) is not None:
            return await interaction.response.send_message(
                f"❌ Слот **{self.slot_num}** уже занят!", ephemeral=True
            )
        else:
            # Освободить предыдущий слот если есть
            for s, uid in slots.items():
                if uid == user_id:
                    slots[s] = None
                    break
            if user_id in reserve:
                reserve.remove(user_id)
            slots[self.slot_num] = user_id
            msg_text = f"✅ Вы заняли слот **{self.slot_num}**!"

        save_data()
        new_view = EventView(self.message_id)
        embed    = build_event_embed(interaction.guild_id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
        await interaction.response.defer()
        await interaction.message.edit(embed=embed, view=new_view)
        await update_thread_list(self.message_id)
        await interaction.followup.send(msg_text, ephemeral=True)


class ReserveButton(ui.Button):
    """Кнопка записи в резерв (запасной список)."""
    def __init__(self, message_id: int):
        super().__init__(
            label="Резерв",
            emoji="🪑",
            style=discord.ButtonStyle.secondary,
            custom_id=f"reserve_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор уже недоступен!", ephemeral=True)
        if data.get("closed"):
            return await interaction.response.send_message("🔒 Список закрыт!", ephemeral=True)

        user_id = interaction.user.id
        slots = data["slots"]
        reserve = data.setdefault("reserve", [])
        join_mode = data.get("mode") == "join"

        if user_id in reserve:
            reserve.remove(user_id)
            msg_text = "❌ Вы покинули **резерв**"
        else:
            for s, uid in list(slots.items()):
                if uid == user_id:
                    slots[s] = None
            if user_id not in reserve:
                reserve.append(user_id)
            msg_text = "✅ Вы записались в **резерв**!"

        save_data()
        embed = build_event_embed(
            interaction.guild_id, data["title"], data["max"], slots,
            data.get("image_url"), data.get("note"), join_mode=join_mode,
            event_time=data.get("event_time"), closed=data.get("closed", False),
            reserve=reserve,
        )
        view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
        await interaction.response.defer()
        await interaction.message.edit(embed=embed, view=view)
        await update_thread_list(self.message_id)
        await interaction.followup.send(msg_text, ephemeral=True)


class DeleteImageButton(ui.Button):
    """Кнопка-корзина: удаляет прикреплённое к сбору фото из эмбеда."""
    def __init__(self, message_id: int):
        super().__init__(
            emoji="🗑️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"delimg_{message_id}",
            row=4,
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор уже недоступен!", ephemeral=True)
        if not data.get("image_url"):
            return await interaction.response.send_message("❌ Фото уже удалено.", ephemeral=True)
        if not can_manage_event_message(interaction, data):
            return await interaction.response.send_message("❌ Недостаточно прав для удаления фото!", ephemeral=True)

        data["image_url"] = None
        save_data()

        join_mode = data.get("mode") == "join"
        embed = build_event_embed(
            interaction.guild_id, data["title"], data["max"], data["slots"],
            None, data.get("note"), join_mode=join_mode,
            event_time=data.get("event_time"), closed=data.get("closed", False),
            reserve=data.get("reserve", []),
        )
        view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
        await interaction.response.edit_message(embed=embed, view=view, attachments=[])


class EventView(ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        data = event_lists.get(message_id)
        if not data:
            return
        slots     = data.get("slots", {})
        max_count = data.get("max", 0)
        has_image = bool(data.get("image_url"))

        # Макс. 24 слота-кнопки + 1 «Резерв» (лимит Discord — 25).
        # Если есть фото — оставляем ещё одно место под кнопку-корзину.
        slot_count = min(max_count, 23 if has_image else 24)
        for i in range(1, slot_count + 1):
            self.add_item(SlotButton(i, message_id, slots.get(i)))
        self.add_item(ReserveButton(message_id))
        if has_image:
            self.add_item(DeleteImageButton(message_id))


class JoinButton(ui.Button):
    """Одна кнопка ✅ для записи/выхода — используется когда слотов > 24."""
    def __init__(self, message_id: int):
        super().__init__(
            label="Записаться",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"join_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор недоступен!", ephemeral=True)
        
        if data.get("closed"):
            return await interaction.response.send_message("🔒 Список закрыт!", ephemeral=True)
        
        user_id = interaction.user.id
        slots = data["slots"]
        reserve = data.setdefault("reserve", [])

        # Уже записан — выйти
        for slot_num, uid in slots.items():
            if uid == user_id:
                slots[slot_num] = None
                save_data()
                embed = build_event_embed(interaction.guild_id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), join_mode=True, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                await interaction.response.defer()
                await interaction.message.edit(embed=embed)
                await update_thread_list(self.message_id)
                await interaction.followup.send("❌ Вы покинули сбор", ephemeral=True)
                return

        # Найти свободный слот
        for i in range(1, data["max"] + 1):
            if slots.get(i) is None:
                if user_id in reserve:
                    reserve.remove(user_id)
                slots[i] = user_id
                save_data()
                embed = build_event_embed(interaction.guild_id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), join_mode=True, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                await interaction.response.defer()
                await interaction.message.edit(embed=embed)
                await update_thread_list(self.message_id)
                await interaction.followup.send("✅ Вы записались в сбор!", ephemeral=True)
                return

        await interaction.response.send_message("❌ Все места заняты! Запишись в **🪑 Резерв**.", ephemeral=True)


class JoinEventView(ui.View):
    """View с кнопкой ✅ и резервом. Для сборов с > 24 слотами / !list."""
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.add_item(JoinButton(message_id))
        self.add_item(ReserveButton(message_id))
        data = event_lists.get(message_id)
        if data and data.get("image_url"):
            self.add_item(DeleteImageButton(message_id))


class KickButton(ui.Button):
    def __init__(self, message_id: int):
        super().__init__(
            label="Кик из слота",
            emoji="👢",
            style=discord.ButtonStyle.secondary,
            custom_id=f"kick_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор не найден!", ephemeral=True)

        slots = data["slots"]
        reserve = data.setdefault("reserve", [])
        members_in_slots = [(slot_num, uid) for slot_num, uid in slots.items() if uid is not None]
        if not members_in_slots and not reserve:
            return await interaction.response.send_message("❌ Слоты и резерв пусты!", ephemeral=True)

        options = [
            discord.SelectOption(label=f"Слот {s}: {uid}", value=f"S{s}")
            for s, uid in members_in_slots
        ]
        options += [
            discord.SelectOption(label=f"Резерв R{i}: {uid}", value=f"R{i - 1}")
            for i, uid in enumerate(reserve, 1)
        ]
        options = options[:25]

        class KickSelect(ui.Select):
            def __init__(self_inner):
                super().__init__(placeholder="Выбери участника для кика...", options=options)

            async def callback(self_inner, inter: discord.Interaction):
                value = self_inner.values[0]
                if value.startswith("R"):
                    idx = int(value[1:])
                    kicked_uid = reserve.pop(idx) if 0 <= idx < len(reserve) else None
                    where = "резерва"
                else:
                    slot_num = int(value[1:])
                    kicked_uid = data["slots"].get(slot_num)
                    data["slots"][slot_num] = None
                    where = f"слота **{slot_num}**"
                save_data()
                join_mode = data.get("mode") == "join"
                embed = build_event_embed(inter.guild_id, data["title"], data["max"], data["slots"], data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                try:
                    ch = bot.get_channel(data["channel_id"])
                    orig_msg = await ch.fetch_message(self.message_id)
                    view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
                    await orig_msg.edit(embed=embed, view=view)
                except Exception:
                    pass
                await update_thread_list(self.message_id)
                await inter.response.edit_message(content=f"✅ <@{kicked_uid}> убран из {where}", view=None, embed=None)

        kick_view = ui.View(timeout=60)
        kick_view.add_item(KickSelect())
        await interaction.response.send_message("Выбери участника для кика:", view=kick_view, ephemeral=True)


class PromoteButton(ui.Button):
    def __init__(self, message_id: int):
        super().__init__(
            label="Убрать с резерва в основу",
            emoji="⬆️",
            style=discord.ButtonStyle.primary,
            custom_id=f"promote_from_reserve_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор не найден!", ephemeral=True)

        slots = data["slots"]
        reserve = data.setdefault("reserve", [])
        if not reserve:
            return await interaction.response.send_message("❌ Резерв пуст!", ephemeral=True)

        free_slot = next((i for i in range(1, data["max"] + 1) if slots.get(i) is None), None)
        if free_slot is None:
            return await interaction.response.send_message("❌ Нет свободных слотов в основе!", ephemeral=True)

        options = [
            discord.SelectOption(label=f"Резерв R{i}: {uid}", value=str(i - 1))
            for i, uid in enumerate(reserve, 1)
        ][:25]

        class PromoteSelect(ui.Select):
            def __init__(self_inner):
                super().__init__(placeholder="Выбери участника для переноса в основу...", options=options)

            async def callback(self_inner, inter: discord.Interaction):
                idx = int(self_inner.values[0])
                if not (0 <= idx < len(reserve)):
                    return await inter.response.edit_message(content="❌ Устарело, попробуй снова", view=None)
                target_slot = next((i for i in range(1, data["max"] + 1) if slots.get(i) is None), None)
                if target_slot is None:
                    return await inter.response.edit_message(content="❌ Нет свободных слотов в основе!", view=None)
                promoted_uid = reserve.pop(idx)
                slots[target_slot] = promoted_uid
                save_data()
                join_mode = data.get("mode") == "join"
                embed = build_event_embed(inter.guild_id, data["title"], data["max"], data["slots"], data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=reserve)
                try:
                    ch = bot.get_channel(data["channel_id"])
                    orig_msg = await ch.fetch_message(self.message_id)
                    view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
                    await orig_msg.edit(embed=embed, view=view)
                except Exception:
                    pass
                await update_thread_list(self.message_id)
                await inter.response.edit_message(content=f"✅ <@{promoted_uid}> перенесён из резерва в слот **{target_slot}**", view=None, embed=None)

        promote_view = ui.View(timeout=60)
        promote_view.add_item(PromoteSelect())
        await interaction.response.send_message("Выбери участника для переноса в основу:", view=promote_view, ephemeral=True)


class CloseListButton(ui.Button):
    def __init__(self, message_id: int, is_closed: bool):
        super().__init__(
            label="🔓 Открыть список" if is_closed else "🔒 Закрыть список",
            style=discord.ButtonStyle.secondary if is_closed else discord.ButtonStyle.danger,
            custom_id=f"close_list_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
        data = event_lists.get(self.message_id)
        if not data:
            return await interaction.response.send_message("❌ Сбор не найден!", ephemeral=True)

        data["closed"] = not data.get("closed", False)
        save_data()

        join_mode = data.get("mode") == "join"
        embed = build_event_embed(interaction.guild_id, data["title"], data["max"], data["slots"], data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data["closed"], reserve=data.get("reserve", []))
        try:
            ch = bot.get_channel(data["channel_id"])
            orig_msg = await ch.fetch_message(self.message_id)
            view = JoinEventView(self.message_id) if join_mode else EventView(self.message_id)
            await orig_msg.edit(embed=embed, view=view)
        except Exception:
            pass

        new_thread_view = ThreadListView(self.message_id)
        try:
            thread = bot.get_channel(data["thread_id"])
            if thread:
                msg = await thread.fetch_message(data["thread_msg_id"])
                await msg.edit(content=build_thread_list(data["title"], data["max"], data["slots"], data.get("reserve", [])), view=new_thread_view)
        except Exception:
            pass

        status = "закрыт 🔒" if data["closed"] else "открыт 🔓"
        await interaction.response.send_message(f"✅ Список {status}", ephemeral=True)


class ThreadListView(ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        data = event_lists.get(message_id)
        self.add_item(KickButton(message_id))
        self.add_item(PromoteButton(message_id))
        self.add_item(CloseListButton(message_id, (data or {}).get("closed", False)))


@bot.command(name="роль_реаки")
async def set_event_role(ctx, роль: discord.Role):
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    """!роль_реаки @роль — настроить роль для тега при !vzp и !list"""
    event_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль настроена",
        description=f"При каждом `!vzp` и `!list` будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


async def _create_event_message(channel, guild, title: str, max_count: int, image_file=None, image_ref: str | None = None, content: str | None = None, force_join_mode: bool = False, event_time: str = None, cmd: str | None = None, event_datetime: str | None = None):
    """Создаёт сбор: эмбед + тред. <= 24 слотов → кнопки-цифры, > 24 → одна кнопка ✅."""
    if not (1 <= max_count <= 100):
        await channel.send("❌ Количество слотов: от 1 до 100!", delete_after=5)
        return

    join_mode = force_join_mode or max_count > 24
    slots     = {i: None for i in range(1, max_count + 1)}

    embed = build_event_embed(guild.id, title, max_count, slots, image_ref, join_mode=join_mode, event_time=event_time)

    if image_file:
        msg = await channel.send(content=content, embed=embed, file=image_file)
    else:
        msg = await channel.send(content=content, embed=embed)

    # Для последующих редактирований используем URL из вложения сообщения (стабильнее)
    if image_ref and msg.attachments:
        image_ref = msg.attachments[0].url
        embed.set_image(url=image_ref)

    event_lists[msg.id] = {
        "title": title, "max": max_count, "mode": "join" if join_mode else "buttons",
        "slots": slots, "reserve": [], "image_url": image_ref, "note": None,
        "channel_id": channel.id, "thread_id": None, "thread_msg_id": None,
        "event_time": event_time, "closed": False, "cmd": cmd,
        "created_at": now_msk().isoformat(), "reminded": False,
        "event_datetime": event_datetime,
    }

    view = JoinEventView(msg.id) if join_mode else EventView(msg.id)
    await msg.edit(embed=embed, view=view)

    # Тред с живым списком
    try:
        hint = (
            "Нажми ✅ для записи · 🪑 Резерв — запасной список\n"
            "Или напиши номер слота (например `23`) прямо в этом треде, чтобы занять его."
            if join_mode else
            "Кнопка слота · 🪑 Резерв — запасной список\n"
            "Или напиши номер слота (например `23`) прямо в этом треде, чтобы занять его."
        )
        thread = await msg.create_thread(name=f"💬 {title}", auto_archive_duration=1440)
        thread_embed = discord.Embed(
            description=f"📋 Обсуждение сбора **{title}**\n{hint}",
            color=discord.Color.blurple(),
        )
        thread_embed.set_footer(text="MORIARTY", icon_url=_footer(guild.id))
        await thread.send(embed=thread_embed)
        list_msg = await thread.send(build_thread_list(title, max_count, slots, []), view=ThreadListView(msg.id))
        event_lists[msg.id]["thread_id"]     = thread.id
        event_lists[msg.id]["thread_msg_id"] = list_msg.id
    except Exception:
        pass
    save_data()


async def _extract_event_image(ctx) -> tuple[discord.File | None, str | None]:
    """Достаёт вложенное к сообщению фото и готовит его для встраивания в эмбед сбора."""
    if not ctx.message.attachments:
        return None, None
    att = ctx.message.attachments[0]
    try:
        img_bytes = await att.read()
        ext       = att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else "png"
        safe_name = f"event_image.{ext}"
        return discord.File(io.BytesIO(img_bytes), filename=safe_name), f"attachment://{safe_name}"
    except Exception:
        return None, None


def _extract_event_time(название: str, default_title: str) -> tuple[str | None, str]:
    """Вытаскивает ЧЧ:ММ из начала названия сбора, если оно там указано."""
    m = re.match(r'^(\d{1,2}:\d{2})\s*(.*)', название)
    if not m:
        return None, название
    return m.group(1), (m.group(2).strip() or default_title)


def _event_mentions(guild: discord.Guild, role_ids: list) -> str | None:
    """Собирает упоминания ролей для тега в сообщении сбора."""
    mentions = []
    for rid in role_ids:
        if not rid:
            continue
        r = guild.get_role(rid)
        if r:
            mentions.append(r.mention)
    return " ".join(mentions) if mentions else None


@bot.command(name="vzp")
async def взп_cmd(ctx, количество: int = 10, *, название: str = "ВЗП"):
    """!vzp [количество] [название] — сбор с фото (от лица бота)"""
    if not can_run_event(ctx, "vzp"):
        return await ctx.message.delete()

    image_file, image_ref = await _extract_event_image(ctx)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    event_time, название = _extract_event_time(название, "ВЗП")

    # Тег: роль ВЗП + роль МП + вторые роли
    content = _event_mentions(ctx.guild, [
        vzp_roles.get(ctx.guild.id),
        vzp_roles2.get(ctx.guild.id),
        mp_roles.get(ctx.guild.id),
        mp_roles2.get(ctx.guild.id),
    ])

    await _create_event_message(ctx.channel, ctx.guild, название, количество, image_file, image_ref, content=content, event_time=event_time, cmd="vzp")


@bot.command(name="vzh")
async def взх_cmd(ctx, *, args: str = ""):
    """!vzh <ЧЧ:ММ> [количество] [название] — сбор ВЗХ; за 30 минут до времени всем занявшим слот придёт напоминание в ЛС"""
    if not can_run_event(ctx, "vzh"):
        return await ctx.message.delete()

    event_time = None
    количество = 10
    название = "ВЗХ"
    m = re.match(r'^(\d{1,2}:\d{2})(?:\s+(\d+))?(?:\s+(.*))?$', args.strip())
    if m:
        event_time = m.group(1)
        if m.group(2):
            количество = int(m.group(2))
        if m.group(3) and m.group(3).strip():
            название = m.group(3).strip()

    if not event_time:
        try:
            await ctx.message.delete()
        except Exception:
            pass
        return await ctx.send(
            "⚠️ Укажи время сбора первым: `!vzh 18:30 [количество] [название]`",
            delete_after=8,
        )

    image_file, image_ref = await _extract_event_image(ctx)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    content = _event_mentions(ctx.guild, [
        vzh_roles.get(ctx.guild.id),
        vzh_roles2.get(ctx.guild.id),
    ])

    hour, minute = map(int, event_time.split(":"))
    faction = get_vzh_faction(ctx.guild.id)
    event_datetime = next_vzh_datetime(now_msk(), hour, minute, faction).isoformat()

    await _create_event_message(ctx.channel, ctx.guild, название, количество, image_file, image_ref, content=content, force_join_mode=True, event_time=event_time, cmd="vzh", event_datetime=event_datetime)


@bot.command(name="роль_взх")
async def set_vzh_role(ctx, роль: discord.Role):
    """!роль_взх @роль — настроить роль ВЗХ для тега в !vzh"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    vzh_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль ВЗХ настроена",
        description=f"В `!vzh` будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_взх2")
async def set_vzh_role2(ctx, роль: discord.Role):
    """!роль_взх2 @роль — вторая роль для тега в !vzh"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    vzh_roles2[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль ВЗХ-2 настроена",
        description=f"В `!vzh` дополнительно будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_взп")
async def set_vzp_role(ctx, роль: discord.Role):
    """!роль_взп @роль — настроить роль ВЗП для тега в !vzp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    vzp_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль ВЗП настроена",
        description=f"В `!vzp` будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_мп")
async def set_mp_role(ctx, роль: discord.Role):
    """!роль_мп @роль — настроить роль МП для тега в !vzp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    mp_roles[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль МП настроена",
        description=f"В `!vzp` будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_взп2")
async def set_vzp_role2(ctx, роль: discord.Role):
    """!роль_взп2 @роль — вторая роль для тега в !vzp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    vzp_roles2[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль ВЗП-2 настроена",
        description=f"В `!vzp` дополнительно будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_мп2")
async def set_mp_role2(ctx, роль: discord.Role):
    """!роль_мп2 @роль — вторая роль для тега в !vzp"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    mp_roles2[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль МП-2 настроена",
        description=f"В `!vzp` дополнительно будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@bot.command(name="роль_реаки2")
async def set_list_role2(ctx, роль: discord.Role):
    """!роль_реаки2 @роль — вторая роль для тега в !list"""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()
    list_roles2[ctx.guild.id] = роль.id
    save_data()
    embed = discord.Embed(
        title="✅ Роль Реаки-2 настроена",
        description=f"В `!list` дополнительно будет тегаться {роль.mention}",
        color=discord.Color.green(),
    )
    embed.set_footer(text="MORIARTY", icon_url=_footer(ctx.guild.id))
    await ctx.send(embed=embed, delete_after=10)
    await ctx.message.delete()


@tree.command(name="доступ_сбора", description="Добавить роль с доступом к команде сбора")
@app_commands.describe(
    тип="Тип сбора: vzp, list или vzh",
    роль="Роль, которая получит доступ к команде"
)
@app_commands.choices(тип=[
    app_commands.Choice(name="vzp", value="vzp"),
    app_commands.Choice(name="list", value="list"),
    app_commands.Choice(name="reaki", value="reaki"),
    app_commands.Choice(name="vzh", value="vzh"),
])
async def slash_event_access_add(interaction: discord.Interaction, тип: str, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    if gid not in event_command_roles:
        event_command_roles[gid] = {}
    if тип not in event_command_roles[gid]:
        event_command_roles[gid][тип] = []
    if роль.id not in event_command_roles[gid][тип]:
        event_command_roles[gid][тип].append(роль.id)
    save_data()
    roles_list = ", ".join(f"<@&{rid}>" for rid in event_command_roles[gid][тип])
    await interaction.response.send_message(
        f"✅ {роль.mention} теперь может использовать `!{тип}`\nВсе роли с доступом: {roles_list}",
        ephemeral=True,
    )


@tree.command(name="убрать_доступ_сбора", description="Убрать роль из доступа к команде сбора")
@app_commands.describe(
    тип="Тип сбора: vzp, list или vzh",
    роль="Роль, которую убрать"
)
@app_commands.choices(тип=[
    app_commands.Choice(name="vzp", value="vzp"),
    app_commands.Choice(name="list", value="list"),
    app_commands.Choice(name="reaki", value="reaki"),
    app_commands.Choice(name="vzh", value="vzh"),
])
async def slash_event_access_remove(interaction: discord.Interaction, тип: str, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid = interaction.guild_id
    allowed = event_command_roles.get(gid, {}).get(тип, [])
    if роль.id not in allowed:
        return await interaction.response.send_message(f"❌ {роль.mention} и так не в списке для `!{тип}`.", ephemeral=True)
    allowed.remove(роль.id)
    save_data()
    await interaction.response.send_message(f"✅ {роль.mention} убрана из доступа к `!{тип}`.", ephemeral=True)


@bot.command(name="list")
async def реаки_cmd(ctx, количество: int = 10, *, название: str = "Реакции"):
    """!list [количество] [название] — сбор на мероприятие (от лица бота)"""
    if not can_run_event(ctx, "list"):
        return await ctx.message.delete()

    image_file, image_ref = await _extract_event_image(ctx)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    event_time, название = _extract_event_time(название, "Реакции")

    content = _event_mentions(ctx.guild, [
        event_roles.get(ctx.guild.id),
        list_roles2.get(ctx.guild.id),
    ])

    await _create_event_message(ctx.channel, ctx.guild, название, количество, image_file, image_ref, content=content, force_join_mode=True, event_time=event_time, cmd="list")


@bot.command(name="spisok")
async def spisok_cmd(ctx):
    """!spisok — ответом (reply) на своё сообщение собирает список тех, кто поставил реакцию"""
    if not can_run_event(ctx, "list"):
        return await ctx.message.delete()

    ref = ctx.message.reference
    if not ref or not ref.message_id:
        return await ctx.send("❌ Используй эту команду **ответом** (reply) на сообщение с реакциями!", delete_after=8)

    try:
        target = ref.cached_message or await ctx.channel.fetch_message(ref.message_id)
    except Exception:
        return await ctx.send("❌ Не удалось найти исходное сообщение.", delete_after=8)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    users = set()
    for reaction in target.reactions:
        async for user in reaction.users():
            if not user.bot:
                users.add(user.id)

    if not users:
        return await ctx.send("❌ На это сообщение ещё никто не отреагировал.", delete_after=8)

    sorted_ids = sorted(users)
    lines = [f"`{str(i).zfill(2)}.` <@{uid}>" for i, uid in enumerate(sorted_ids, 1)]
    text = "📋 **Список отреагировавших**\n" + "\n".join(lines) + f"\n\n**Всего: {len(sorted_ids)}**"
    await ctx.send(text)


def _can_run_event_slash(interaction, event_type):
    if is_admin(interaction):
        return True
    allowed = event_command_roles.get(interaction.guild_id, {}).get(event_type, [])
    return any(r.id in allowed for r in interaction.user.roles)


@tree.command(name="vzp", description="Создать сбор ВЗП")
@app_commands.describe(
    количество="Максимум участников (по умолчанию 10)",
    название="Название сбора (по умолчанию ВЗП)",
    время="Время сбора (например 20:00)",
    картинка="Прикрепить изображение к сбору",
)
async def slash_vzp(
    interaction: discord.Interaction,
    количество: app_commands.Range[int, 1] = 10,
    название: str = "ВЗП",
    время: str = None,
    картинка: discord.Attachment = None,
):
    if not _can_run_event_slash(interaction, "vzp"):
        return await interaction.response.send_message("❌ Нет доступа к этой команде.", ephemeral=True)
    await interaction.response.defer()
    image_file, image_ref = None, None
    if картинка:
        try:
            img_bytes = await картинка.read()
            ext = картинка.filename.rsplit(".", 1)[-1].lower() if "." in картинка.filename else "png"
            safe_name = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref = f"attachment://{safe_name}"
        except Exception:
            pass
    mentions = []
    for rid in [vzp_roles.get(interaction.guild_id), vzp_roles2.get(interaction.guild_id), mp_roles.get(interaction.guild_id), mp_roles2.get(interaction.guild_id)]:
        if rid:
            r = interaction.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None
    await interaction.delete_original_response()
    await _create_event_message(interaction.channel, interaction.guild, название, количество, image_file, image_ref, content=content, event_time=время, cmd="vzp")


@tree.command(name="list", description="Создать сбор на мероприятие (реакции)")
@app_commands.describe(
    количество="Максимум участников (по умолчанию 10)",
    название="Название сбора (по умолчанию Реакции)",
    время="Время сбора (например 20:00)",
    картинка="Прикрепить изображение к сбору",
)
async def slash_list(
    interaction: discord.Interaction,
    количество: app_commands.Range[int, 1] = 10,
    название: str = "Реакции",
    время: str = None,
    картинка: discord.Attachment = None,
):
    if not _can_run_event_slash(interaction, "list"):
        return await interaction.response.send_message("❌ Нет доступа к этой команде.", ephemeral=True)
    await interaction.response.defer()
    image_file, image_ref = None, None
    if картинка:
        try:
            img_bytes = await картинка.read()
            ext = картинка.filename.rsplit(".", 1)[-1].lower() if "." in картинка.filename else "png"
            safe_name = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref = f"attachment://{safe_name}"
        except Exception:
            pass
    mentions = []
    for rid in [event_roles.get(interaction.guild_id), list_roles2.get(interaction.guild_id)]:
        if rid:
            r = interaction.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None
    await interaction.delete_original_response()
    await _create_event_message(interaction.channel, interaction.guild, название, количество, image_file, image_ref, content=content, force_join_mode=True, event_time=время, cmd="list")


@tree.command(name="reaki", description="Создать сбор реакций (тегает роль реаки)")
@app_commands.describe(
    количество="Максимум участников (по умолчанию 10)",
    название="Название сбора (по умолчанию Реакции)",
    время="Время сбора (например 20:00)",
    картинка="Прикрепить изображение к сбору",
)
async def slash_reaki(
    interaction: discord.Interaction,
    количество: app_commands.Range[int, 1] = 10,
    название: str = "Реакции",
    время: str = None,
    картинка: discord.Attachment = None,
):
    if not _can_run_event_slash(interaction, "reaki"):
        return await interaction.response.send_message("❌ Нет доступа к этой команде.", ephemeral=True)
    await interaction.response.defer()
    image_file, image_ref = None, None
    if картинка:
        try:
            img_bytes = await картинка.read()
            ext = картинка.filename.rsplit(".", 1)[-1].lower() if "." in картинка.filename else "png"
            safe_name = f"event_image.{ext}"
            image_file = discord.File(io.BytesIO(img_bytes), filename=safe_name)
            image_ref = f"attachment://{safe_name}"
        except Exception:
            pass
    mentions = []
    for rid in [event_roles.get(interaction.guild_id), list_roles2.get(interaction.guild_id)]:
        if rid:
            r = interaction.guild.get_role(rid)
            if r:
                mentions.append(r.mention)
    content = " ".join(mentions) if mentions else None
    await interaction.delete_original_response()
    await _create_event_message(interaction.channel, interaction.guild, название, количество, image_file, image_ref, content=content, force_join_mode=True, event_time=время, cmd="list")


@bot.command(name="замена")
async def замена_cmd(ctx, кого: int, на_кого: int = 0):
    """!замена <айди_кого> <айди_на_кого> — заменить участника в слоте (0 = убрать). Используется в треде сбора."""
    if not is_admin_ctx(ctx):
        return await ctx.message.delete()

    # Найти сбор по thread_id
    thread_id = ctx.channel.id
    msg_id = None
    for mid, data in event_lists.items():
        if data.get("thread_id") == thread_id:
            msg_id = mid
            break

    if msg_id is None:
        return await ctx.send("❌ Эта команда используется только в треде сбора!", delete_after=6)

    data  = event_lists[msg_id]
    slots = data["slots"]

    # Найти слот кого
    target_slot = None
    for slot_num, uid in slots.items():
        if uid == кого:
            target_slot = slot_num
            break

    if target_slot is None:
        return await ctx.send(f"❌ Пользователь `{кого}` не найден ни в одном слоте!", delete_after=6)

    # Если на_кого уже занимает другой слот — освободить его
    if на_кого:
        for slot_num, uid in slots.items():
            if uid == на_кого:
                slots[slot_num] = None
                break

    slots[target_slot] = на_кого if на_кого else None

    # Обновить эмбед
    try:
        channel = bot.get_channel(data["channel_id"])
        msg = await channel.fetch_message(msg_id)
        join_mode = data.get("mode") == "join"
        embed = build_event_embed(ctx.guild.id, data["title"], data["max"], slots, data.get("image_url"), data.get("note"), join_mode=join_mode, event_time=data.get("event_time"), closed=data.get("closed", False), reserve=data.get("reserve", []))
        view = JoinEventView(msg_id) if join_mode else EventView(msg_id)
        await msg.edit(embed=embed, view=view)
    except Exception:
        pass

    await update_thread_list(msg_id)

    if на_кого:
        await ctx.send(f"✅ Слот **{target_slot}**: <@{кого}> → <@{на_кого}>", delete_after=10)
    else:
        await ctx.send(f"✅ Слот **{target_slot}**: <@{кого}> убран", delete_after=10)
    await ctx.message.delete()
