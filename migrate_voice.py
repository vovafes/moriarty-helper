import json

DATA_FILE   = "data.json"
POINTS_FILE = "points.json"
DEFAULT_RATE = 10  # 💎 за минуту если не настроено

with open(DATA_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

voice_minutes     = data.get("voice_minutes", {})
voice_settings    = data.get("voice_reward_settings", {})

try:
    with open(POINTS_FILE, "r", encoding="utf-8") as f:
        points_data = json.load(f)
except FileNotFoundError:
    points_data = {"points": {}}

points = points_data.get("points", {})

total_awarded = 0
total_users   = 0

for g_str, users in voice_minutes.items():
    rate = voice_settings.get(g_str, {}).get("amount", DEFAULT_RATE)

    if g_str not in points:
        points[g_str] = {}

    for u_str, minutes in users.items():
        if minutes <= 0:
            continue
        earned = minutes * rate
        current = points[g_str].get(u_str, 0)
        points[g_str][u_str] = current + earned
        print(f"  Гильдия {g_str} | <@{u_str}>: +{earned} 💎 ({minutes} мин × {rate})")
        total_awarded += earned
        total_users   += 1

points_data["points"] = points
with open(POINTS_FILE, "w", encoding="utf-8") as f:
    json.dump(points_data, f, ensure_ascii=False)

print(f"\n✅ Готово: {total_users} участников, итого +{total_awarded} 💎 начислено.")
print(f"   Сохранено в {POINTS_FILE}")
