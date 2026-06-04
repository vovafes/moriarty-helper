import json
import os

POINTS_FILE = "points.json"
CHIPS_FILE  = "chips.json"

# Читаем алмазы
if not os.path.exists(POINTS_FILE):
    print(f"❌ {POINTS_FILE} не найден — нечего переносить.")
    exit(1)

with open(POINTS_FILE, "r", encoding="utf-8") as f:
    points_data = json.load(f)

diamonds = points_data.get("points", {})

# Читаем текущие фишки (если файл уже есть)
if os.path.exists(CHIPS_FILE):
    with open(CHIPS_FILE, "r", encoding="utf-8") as f:
        chips_data = json.load(f)
else:
    chips_data = {"chips": {}}

chips = chips_data.get("chips", {})

total_users  = 0
total_chips  = 0

for g_str, users in diamonds.items():
    if g_str not in chips:
        chips[g_str] = {}
    for u_str, amount in users.items():
        if amount <= 0:
            continue
        current = chips[g_str].get(u_str, 0)
        chips[g_str][u_str] = current + amount
        print(f"  Гильдия {g_str} | <@{u_str}>: +{amount} 🎰 (было {current} → стало {current + amount})")
        total_users += 1
        total_chips += amount

chips_data["chips"] = chips
with open(CHIPS_FILE, "w", encoding="utf-8") as f:
    json.dump(chips_data, f, ensure_ascii=False)

print(f"\n✅ Готово: {total_users} участников, итого {total_chips} 🎰 начислено.")
print(f"   Алмазы 💎 не тронуты. Фишки сохранены в {CHIPS_FILE}")
