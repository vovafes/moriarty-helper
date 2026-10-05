"""shop -- split out of main.py."""

from datetime import datetime

import discord
from discord import app_commands, ui
from legacy.app import tree
from legacy.helpers import (
    _footer,
    add_points,
    build_shop_embed,
    decrement_warn,
    get_points,
    get_warns,
    is_admin,
)
from legacy.recruit import (
    _delete_warn_log_message,
)
from legacy.state import (
    guild_shop_items,
    save_data,
    shop_log_channels,
    shop_manager_roles,
    shop_panels,
    warn_roles,
)


async def refresh_shop_message(guild: discord.Guild):
    panel = shop_panels.get(guild.id)
    if not panel:
        return
    try:
        channel = guild.get_channel(panel["channel_id"])
        msg     = await channel.fetch_message(panel["message_id"])
        view    = ShopView(guild.id)
        await msg.edit(embed=build_shop_embed(guild.id), view=view)
    except Exception:
        pass


ACTION_LABELS = {
    "remove_warn": "🗑 Снятие варна",
    "give_role":   "🎭 Выдача роли",
    "notify":      "📦 Ручная выдача",
}


async def _log_shop_purchase(
    interaction: discord.Interaction,
    item: dict,
    price: int,
    action: str,
    extra: str | None = None,
):
    """Отправляет лог покупки в shop_log_channels.
    Для action='notify' тегает shop_manager_roles — нужна ручная выдача."""
    guild_id = interaction.guild_id
    log_ch_id = shop_log_channels.get(guild_id)
    if not log_ch_id:
        return
    log_ch = interaction.guild.get_channel(log_ch_id)
    if not log_ch:
        return

    embed = discord.Embed(
        title="🛒 Покупка в магазине",
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="Покупатель", value=interaction.user.mention, inline=True)
    embed.add_field(name="Товар", value=f"{item.get('emoji', '')} {item['name']}".strip(), inline=True)
    embed.add_field(name="Цена", value=f"**{price}** 💎", inline=True)
    embed.add_field(name="Тип", value=ACTION_LABELS.get(action, action), inline=True)
    if extra:
        embed.add_field(name="Выдано", value=extra, inline=True)
    embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))

    # Тег роли только для ручной выдачи
    content = None
    if action == "notify":
        mgr_role_id = shop_manager_roles.get(guild_id)
        if mgr_role_id:
            content = f"<@&{mgr_role_id}>"

    try:
        await log_ch.send(content=content, embed=embed)
    except Exception:
        pass


class ShopItemButton(ui.Button):
    def __init__(self, item_id: str, item: dict, guild_id: int):
        super().__init__(
            label=item["name"],
            emoji=item.get("emoji") or None,
            style=discord.ButtonStyle.primary,
            custom_id=f"shop_{guild_id}_{item_id}",
        )
        self.item_id  = item_id
        self.item     = item
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id
        item     = guild_shop_items.get(guild_id, {}).get(self.item_id)

        if not item:
            return await interaction.response.send_message("❌ Товар больше не доступен.", ephemeral=True)

        price  = item["price"]
        points = get_points(guild_id, user_id)

        if points < price:
            return await interaction.response.send_message(
                f"❌ Недостаточно баллов! Нужно **{price}** 💎, у вас **{points}** 💎",
                ephemeral=True,
            )

        action = item.get("action", "notify")

        if action == "remove_warn":
            warn_data = get_warns(guild_id, user_id)
            if not warn_data:
                return await interaction.response.send_message("✅ У вас нет варнов для снятия!", ephemeral=True)
            if warn_data.get("payment_method") == "money":
                return await interaction.response.send_message(
                    "❌ Этот варн можно снять только деньгами. Обратитесь к администрации.", ephemeral=True
                )

            new_level = decrement_warn(guild_id, user_id)

            guild_warn_roles = warn_roles.get(guild_id, {})
            roles_to_remove = [interaction.guild.get_role(rid) for rid in guild_warn_roles.values() if interaction.guild.get_role(rid)]
            new_role = interaction.guild.get_role(guild_warn_roles.get(new_level)) if new_level > 0 else None
            try:
                await interaction.user.remove_roles(*[r for r in roles_to_remove if r and r != new_role], reason="Покупка: снятие варна")
                if new_role:
                    await interaction.user.add_roles(new_role, reason=f"Warn {new_level}/3")
            except Exception:
                pass

            await _delete_warn_log_message(interaction.guild, user_id)

            add_points(guild_id, user_id, -price)
            await _log_shop_purchase(interaction, item, price, action="remove_warn")
            desc = f"Списано **{price}** 💎" + (
                f"\nОсталось варнов: **{new_level}/3**" if new_level > 0 else "\nВарнов больше нет!"
            )
            embed = discord.Embed(title="✅ Варн снят!", description=desc, color=discord.Color.green(), timestamp=datetime.now())
            embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "give_role":
            role_id = item.get("role_id")
            role    = interaction.guild.get_role(role_id) if role_id else None
            if not role:
                return await interaction.response.send_message("❌ Роль не найдена. Обратитесь к администратору.", ephemeral=True)
            try:
                await interaction.user.add_roles(role, reason=f"Покупка в магазине: {item['name']}")
            except Exception:
                return await interaction.response.send_message("❌ Не удалось выдать роль.", ephemeral=True)
            add_points(guild_id, user_id, -price)
            await _log_shop_purchase(interaction, item, price, action="give_role", extra=role.mention)
            embed = discord.Embed(title=f"✅ Куплено: {item['name']}", description=f"Роль {role.mention} выдана!\nСписано **{price}** 💎", color=discord.Color.green(), timestamp=datetime.now())
            embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        else:  # notify — ручная выдача
            add_points(guild_id, user_id, -price)
            await _log_shop_purchase(interaction, item, price, action="notify")
            embed = discord.Embed(
                title=f"✅ Куплено: {item['name']}",
                description=f"Списано **{price}** 💎\nАдминистратор скоро свяжется с вами.",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            embed.set_footer(text="MORIARTY", icon_url=_footer(guild_id))
            return await interaction.response.send_message(embed=embed, ephemeral=True)


class ShopView(ui.View):
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        items = guild_shop_items.get(guild_id, {})
        for item_id, item in items.items():
            self.add_item(ShopItemButton(item_id, item, guild_id))


@tree.command(name="магазин", description="Развернуть панель магазина в этом канале")
async def slash_shop(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid  = interaction.guild_id
    embed = build_shop_embed(gid)
    view  = ShopView(gid)
    msg   = await interaction.channel.send(embed=embed, view=view)
    shop_panels[gid] = {"channel_id": interaction.channel_id, "message_id": msg.id}
    save_data()
    await interaction.response.send_message("✅ Панель магазина развёрнута!", ephemeral=True)


class AddItemModal(ui.Modal, title="🛒 Добавить товар"):
    name        = ui.TextInput(label="Название товара", placeholder="Снять варн", required=True)
    price       = ui.TextInput(label="Цена (баллы)", placeholder="500", required=True)
    emoji       = ui.TextInput(label="Эмодзи", placeholder="⚠️", required=False, max_length=8)
    description = ui.TextInput(label="Описание", placeholder="Снимает один варн", required=False)
    action      = ui.TextInput(
        label="Действие: remove_warn / give_role / notify",
        placeholder="notify",
        required=True,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        try:
            price_val = int(str(self.price).strip())
        except ValueError:
            return await interaction.response.send_message("❌ Цена должна быть числом.", ephemeral=True)

        action_val = str(self.action).strip().lower()
        if action_val not in ("remove_warn", "give_role", "notify"):
            return await interaction.response.send_message(
                "❌ Действие должно быть: `remove_warn`, `give_role` или `notify`", ephemeral=True
            )

        if gid not in guild_shop_items:
            guild_shop_items[gid] = {}

        import time
        item_id = str(int(time.time()))
        guild_shop_items[gid][item_id] = {
            "name":        str(self.name).strip(),
            "price":       price_val,
            "emoji":       str(self.emoji).strip() or "🛒",
            "description": str(self.description).strip(),
            "action":      action_val,
            "role_id":     None,
        }
        save_data()
        await refresh_shop_message(interaction.guild)
        await interaction.response.send_message(
            f"✅ Товар **{self.name}** добавлен!\n"
            f"Если действие `give_role` — используй `/товар_роль {item_id} @роль` для привязки роли.",
            ephemeral=True,
        )


@tree.command(name="добавить_товар", description="Добавить товар в магазин")
async def slash_add_item(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    await interaction.response.send_modal(AddItemModal())


@tree.command(name="убрать_товар", description="Удалить товар из магазина")
@app_commands.describe(товар_id="ID товара (виден в /список_товаров)")
async def slash_remove_item(interaction: discord.Interaction, товар_id: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid   = interaction.guild_id
    items = guild_shop_items.get(gid, {})
    if товар_id not in items:
        return await interaction.response.send_message("❌ Товар не найден.", ephemeral=True)
    name = items[товар_id]["name"]
    del guild_shop_items[gid][товар_id]
    save_data()
    await refresh_shop_message(interaction.guild)
    await interaction.response.send_message(f"✅ Товар **{name}** удалён.", ephemeral=True)


@tree.command(name="список_товаров", description="Список всех товаров в магазине (с ID)")
async def slash_list_items(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid   = interaction.guild_id
    items = guild_shop_items.get(gid, {})
    if not items:
        return await interaction.response.send_message("Магазин пуст.", ephemeral=True)
    lines = [f"`{iid}` — {i['emoji']} **{i['name']}** | {i['price']} 💎 | `{i['action']}`" for iid, i in items.items()]
    embed = discord.Embed(title="🛒 Товары магазина", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text="Используй ID для /убрать_товар или /товар_роль")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="товар_роль", description="Привязать роль к товару с действием give_role")
@app_commands.describe(товар_id="ID товара", роль="Роль которая выдаётся при покупке")
async def slash_item_role(interaction: discord.Interaction, товар_id: str, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    gid   = interaction.guild_id
    items = guild_shop_items.get(gid, {})
    if товар_id not in items:
        return await interaction.response.send_message("❌ Товар не найден.", ephemeral=True)
    if items[товар_id]["action"] != "give_role":
        return await interaction.response.send_message("❌ Действие товара не `give_role`.", ephemeral=True)
    guild_shop_items[gid][товар_id]["role_id"] = роль.id
    save_data()
    await interaction.response.send_message(f"✅ Роль {роль.mention} привязана к товару **{items[товар_id]['name']}**.", ephemeral=True)


@tree.command(name="лог_магазина", description="Канал, куда пишутся все покупки из магазина")
@app_commands.describe(канал="Текстовый канал для логов покупок")
async def slash_shop_log(interaction: discord.Interaction, канал: discord.TextChannel):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    shop_log_channels[interaction.guild_id] = канал.id
    save_data()
    await interaction.response.send_message(f"✅ Логи покупок будут отправляться в {канал.mention}.", ephemeral=True)


@tree.command(name="роль_магазина", description="Роль, которая тегается при покупках с ручной выдачей (notify)")
@app_commands.describe(роль="Роль ответственного за выдачу товаров")
async def slash_shop_manager_role(interaction: discord.Interaction, роль: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    shop_manager_roles[interaction.guild_id] = роль.id
    save_data()
    await interaction.response.send_message(f"✅ Роль {роль.mention} будет тегаться при покупках с ручной выдачей.", ephemeral=True)
