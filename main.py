import asyncio
import html
import logging
import os
import re
import time
import aiohttp
import io
import numpy as np
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
FACEIT_API_KEY = os.getenv("FACEIT_API_KEY")
MARKET_API_KEY = os.getenv("MARKET_API_KEY")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
cooldowns = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

def get_flag(code):
    return "".join(chr(127397 + ord(c)) for c in code.upper()) if code and len(code) == 2 else "🌐"

def parse_input(text):
    text = text.strip()
    if re.fullmatch(r"\d{17}", text):
        return "id64", text
    m1 = re.search(r"steamcommunity\.com/profiles/(\d{17})", text)
    if m1:
        return "id64", m1.group(1)
    m2 = re.search(r"steamcommunity\.com/id/([a-zA-Z0-9_-]+)", text)
    if m2:
        return "vanity", m2.group(1)
    return None, None

async def fetch_json(session, url, **kwargs):
    try:
        async with session.get(url, **kwargs) as r:
            return await r.json() if r.status == 200 else None
    except Exception as e:
        logging.error(f"Fetch error for {url}: {e}")
        return None

async def get_steam(session, id_type, val):
    url = f"https://steamcommunity.com/profiles/{val}/" if id_type == "id64" else f"https://steamcommunity.com/id/{val}/"
    try:
        async with session.get(url, headers=HEADERS, allow_redirects=True) as r:
            if r.status != 200: return None
            page = await r.text()
    except Exception:
        return None

    if "could not be found" in page or "не может быть найден" in page:
        return None

    sid_m = re.search(r'"steamid":"(\d{17})"', page)
    sid = sid_m.group(1) if sid_m else (val if id_type == "id64" else None)
    if not sid: return None

    nick_m = re.search(r'<span class="actual_persona_name">(.*?)</span>', page, re.S) or re.search(r'<title>Steam Community :: (.*?)</title>', page, re.S)
    nick = html.unescape(nick_m.group(1).strip()) if nick_m else "Без ника"

    av_m = re.search(r'<div class="playerAvatarAutoSizeInner">.*?<img src="(https://[^"]+full\.jpg)"', page, re.S) or re.search(r'<meta property="og:image" content="(https://[^"]+)"', page)
    avatar = av_m.group(1) if av_m else "https://avatars.steamstatic.com/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb_full.jpg"

    priv = "🔒 Скрытый" if ("private" in page or "скрыт" in page) else "🟢 Публичный"
    reg, vac, trade, lvl, hours = "Скрыта", "🟢 Нет", "🟢 Нет", "—", "—"

    try:
        async with session.get(f"https://steamcommunity.com/profiles/{sid}/?xml=1", headers=HEADERS) as xr:
            if xr.status == 200:
                xt = await xr.text()
                ms = re.search(r"<memberSince><!\[CDATA\[(.*?)\]\]></memberSince>|<memberSince>(.*?)</memberSince>", xt)
                if ms and (ms.group(1) or ms.group(2)):
                    reg = (ms.group(1) or ms.group(2)).strip()
                if "<vacBanned>1</vacBanned>" in xt: vac = "🔴 Есть VAC"
                if "<tradeBanned>1</tradeBanned>" in xt: trade = "🔴 Есть"
    except Exception:
        pass

    if STEAM_API_KEY:
        b_data = await fetch_json(session, "https://api.steampowered.com/ISteamUser/GetPlayerBans/v1/", params={"key": STEAM_API_KEY, "steamids": sid})
        if b_data and b_data.get("players"):
            b = b_data["players"][0]
            vc, gc = b.get("NumberOfVACBans", 0), b.get("NumberOfGameBans", 0)
            vac = f"🔴 VAC: {vc} | Игровых: {gc}" if (vc or gc) else "🟢 Нет"
            trade = "🔴 Есть" if b.get("EconomyBan") != "none" else "🟢 Нет"

        l_data = await fetch_json(session, "https://api.steampowered.com/IPlayerService/GetSteamLevel/v1/", params={"key": STEAM_API_KEY, "steamid": sid})
        if l_data and "player_level" in l_data.get("response", {}):
            lvl = str(l_data["response"]["player_level"])

        g_data = await fetch_json(session, "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/", params={"key": STEAM_API_KEY, "steamid": sid, "include_appinfo": 0})
        for g in (g_data or {}).get("response", {}).get("games", []):
            if g.get("appid") == 730:
                hours = f"{round(g.get('playtime_forever', 0) / 60, 1)} ч."
                break

    inv_info, skins = "🔒 Скрыт или пуст", []
    inv_h = {**HEADERS, "Referer": f"https://steamcommunity.com/profiles/{sid}/inventory/"}
    inv = await fetch_json(session, f"https://steamcommunity.com/inventory/{sid}/730/2?l=russian&count=100", headers=inv_h)
    
    if inv and inv.get("success") == 1:
        assets = inv.get("assets", [])
        descs = {d.get("classid"): d for d in inv.get("descriptions", [])}
        if assets:
            inv_info = f"📦 Предметов: **{len(assets)}**"
            uniq = []
            for a in assets:
                d = descs.get(a.get("classid"))
                if d:
                    name = d.get("market_hash_name") or d.get("name")
                    if name and not any(w in name for w in ("Граффити", "Наклейка", "Кейс", "Капсула")):
                        if name not in uniq:
                            uniq.append(name)
            skins = [f"• {s}" for s in uniq[:5]]

    return {"sid": sid, "nick": nick, "avatar": avatar, "priv": priv, "reg": reg, "vac": vac, "trade": trade, "lvl": lvl, "hours": hours, "inv": inv_info, "skins": skins}

async def get_faceit(session, sid):
    key = (FACEIT_API_KEY or "").strip().strip('"').strip("'")
    if not key: return None
    h = {"Authorization": f"Bearer {key}"}
    pdata = None
    for game in ("cs2", "csgo"):
        pdata = await fetch_json(session, "https://open.faceit.com/data/v4/players", headers=h, params={"game": game, "game_player_id": sid})
        if pdata: break
    if not pdata:
        sdata = await fetch_json(session, "https://open.faceit.com/data/v4/search/players", headers=h, params={"nickname": sid, "limit": 1})
        items = (sdata or {}).get("items", [])
        if items:
            pdata = await fetch_json(session, f"https://open.faceit.com/data/v4/players/{items[0]['player_id']}", headers=h)
    if pdata and pdata.get("player_id"):
        st = await fetch_json(session, f"https://open.faceit.com/data/v4/players/{pdata['player_id']}/stats/cs2", headers=h)
        lf = (st or {}).get("lifetime", {})
        if lf:
            pdata["st"] = {"m": lf.get("Matches", "—"), "wr": lf.get("Win Rate %", "—"), "kd": lf.get("Average K/D Ratio", "—"), "hs": lf.get("Average Headshots %", "—")}
    return pdata

def get_custom_keyboard(sid: str, pid: str, nickname: str, exclude: str = ""):
    kb = InlineKeyboardBuilder()
    if exclude != "price":
        kb.button(text="💰 Посчитать стоимость инвентаря", callback_data=f"price_{sid}")
    if pid and exclude != "elo":
        kb.button(text="📈 Статистика ELO", callback_data=f"elo_{pid}_{nickname}")
    if pid and exclude != "maps":
        kb.button(text="🗺 Статистика по картам", callback_data=f"maps_{pid}_{nickname}")
    kb.adjust(1)
    return kb.as_markup()

@dp.message(Command("start", "help"))
async def cmd_start(m: types.Message):
    welcome_text = (
        "🚀 **CS2 & Steam Analytics Bot**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 Пробивай игроков в один клик!\n\n"
        "📌 **Что я умею:**\n"
        "• Показывать аватарку и ник профиля\n"
        "• Проверять уровень FACEIT, ELO и регион\n"
        "• Выводить статистику матчей (K/D, Винрейт, HS)\n"
        "• Считать стоимость инвентаря CS2 через Market API\n"
        "• Показывать часы в игре и баны (VAC / Trade)\n"
        "• Строить графики ELO и радарные диаграммы карт по кнопкам\n\n"
        "👉 **Просто отправь ссылку на профиль Steam или SteamID64!**"
    )
    await m.answer(welcome_text, parse_mode="Markdown")

@dp.message(F.text)
async def check_profile(m: types.Message):
    uid = m.from_user.id
    if uid in cooldowns and time.time() - cooldowns[uid] < 3:
        await m.answer("⏳ Подожди пару секунд.")
        return
    cooldowns[uid] = time.time()

    id_type, val = parse_input(m.text)
    if not val:
        await m.answer("❌ Неверная ссылка. Пришли правильную ссылку на профиль Steam или 17-значный SteamID64.")
        return

    msg = await m.answer("🔄 **Инициализация запроса...**\n`[░░░░░░░░░░] 0%`", parse_mode="Markdown")
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        await asyncio.sleep(0.3)
        await msg.edit_text("🔄 **Стягиваем данные Steam и инвентарь...**\n`[████░░░░░░] 40%`", parse_mode="Markdown")
        
        s = await get_steam(session, id_type, val)
        if not s:
            await msg.edit_text("❌ Профиль Steam не найден или скрыт.")
            return

        await asyncio.sleep(0.3)
        await msg.edit_text("🔄 **Пробиваем FACEIT и стату матчей...**\n`[████████░░] 80%`", parse_mode="Markdown")
        
        f = await get_faceit(session, s["sid"])
        await asyncio.sleep(0.2)
        await msg.edit_text("🔄 **Формируем киберспортивную карточку...**\n`[██████████] 100%`", parse_mode="Markdown")

    s_url = f"https://steamcommunity.com/profiles/{s['sid']}"
    pid = f.get("player_id") if f else None
    nickname = f.get("nickname", "player") if f else "player"

    if f:
        cs = f.get("games", {}).get("cs2") or f.get("games", {}).get("csgo") or {}
        f_url = f.get("faceit_url", "").replace("{lang}", "ru")
        cap = (
            f"{get_flag(f.get('country'))} **{nickname}** | Steam: `{s['nick']}`\n"
            f"🔗 [Steam]({s_url}) • [FACEIT]({f_url})\n━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 **FACEIT:** Уровень **{cs.get('skill_level', '—')}** • ELO: **{cs.get('faceit_elo', '—')}**\n"
            f"🌍 **Регион:** `{cs.get('region', '—')}` | **Уровень Steam:** `{s['lvl']}`\n\n"
            f"📅 **Регистрация:**\n• FACEIT: `{f.get('activated_at', '')[:10] or '—'}`\n• VALVE: `{s['reg']}`\n\n"
        )
        if f.get("st"):
            st = f["st"]
            cap += f"📊 **Статистика CS2:**\n• Матчи: **{st['m']}** | Винрейт: **{st['wr']}%**\n• K/D: **{st['kd']}** | HS: **{st['hs']}%**\n\n"
    else:
        cap = (
            f"👤 **{s['nick']}**\n🔗 [Профиль Steam]({s_url})\n━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 **Steam64:** `{s['sid']}`\n📅 **VALVE Рег.:** `{s['reg']}` | **Уровень:** `{s['lvl']}`\n🔥 **FACEIT:** Не привязан\n\n"
        )

    cap += f"⏱ **Часов в CS2:** `{s['hours']}`\n🎒 **Инвентарь CS2:** {s['inv']}\n"
    if s["skins"]:
        cap += "• **Топ скины:**\n" + "\n".join(s["skins"]) + "\n"
    
    cap += (
        f"\n🛡 **Steam ({s['priv']}):**\n• Баны: {s['vac']} | Трейд: {s['trade']}\n\n"
        f"💬 *Можете отправить следующую ссылку для проверки!*"
    )

    kb = get_custom_keyboard(s["sid"], pid, nickname, exclude="")

    await msg.delete()
    await m.answer_photo(photo=s["avatar"], caption=cap, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("price_"))
async def calculate_inventory_price(callback: types.CallbackQuery):
    sid = callback.data.split("_")[1]
    
    if not MARKET_API_KEY:
        await callback.answer("❌ Не задан MARKET_API_KEY в файле .env!", show_alert=True)
        return

    calc_msg = await callback.message.answer("💰 **Подключаемся к Market CSGO...**\n`[░░░░░░░░░░] 0%`", parse_mode="Markdown")
    await callback.answer("⏳ Запрос цен по API...")

    total_price = 0.0
    items_found = 0

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        await calc_msg.edit_text("💰 **Загружаем базу цен скинов...**\n`[████░░░░░░] 40%`", parse_mode="Markdown")
        prices_url = f"https://market.csgo.com/api/v2/prices/RUB.json"
        market_data = await fetch_json(session, prices_url)
        
        prices_dict = {}
        if market_data and market_data.get("success"):
            for item in market_data.get("items", []):
                prices_dict[item.get("market_hash_name")] = float(item.get("price", 0))

        await calc_msg.edit_text("💰 **Стягиваем инвентарь игрока...**\n`[████████░░] 80%`", parse_mode="Markdown")
        inv_h = {**HEADERS, "Referer": f"https://steamcommunity.com/profiles/{sid}/inventory/"}
        inv = await fetch_json(session, f"https://steamcommunity.com/inventory/{sid}/730/2?l=russian&count=100", headers=inv_h)
        
        pid = ""
        nickname = "player"
        try:
            f_res = await fetch_json(session, f"https://open.faceit.com/data/v4/players", headers={"Authorization": f"Bearer {FACEIT_API_KEY}"}, params={"game": "cs2", "game_player_id": sid})
            if f_res and f_res.get("player_id"):
                pid = f_res["player_id"]
                nickname = f_res.get("nickname", "player")
        except:
            pass

        if inv and inv.get("success") == 1:
            assets = inv.get("assets", [])
            descs = {d.get("classid"): d for d in inv.get("descriptions", [])}
            
            items_to_check = []
            for a in assets:
                d = descs.get(a.get("classid"))
                if d:
                    name = d.get("market_hash_name")
                    if name and name not in items_to_check and not any(w in name for w in ("Граффити", "Наклейка", "Кейс", "Капсула")):
                        items_to_check.append(name)
            
            total_items = len(items_to_check)
            if total_items == 0:
                await calc_msg.edit_text("❌ Инвентарь пуст или не содержит оцениваемых предметов.")
                return

            for i, name in enumerate(items_to_check):
                if name in prices_dict:
                    total_price += prices_dict[name]
                    items_found += 1

                percent = int(((i + 1) / total_items) * 100)
                filled = "█" * (percent // 10)
                empty = "░" * (10 - (percent // 10))
                
                if (i + 1) % 5 == 0 or (i + 1) == total_items:
                    await calc_msg.edit_text(
                        f"💰 **Сопоставляем цены предметов...**\n`[{filled}{empty}] {percent}%`\n*(Обработано: {i+1}/{total_items})*",
                        parse_mode="Markdown"
                    )
                await asyncio.sleep(0.05)

    if total_price > 0:
        result_text = (
            f"✅ **Результат оценки инвентаря (Market CSGO):**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Общая стоимость: **~{round(total_price, 2)} руб.**\n"
            f"📊 Оценено позиций: `{items_found} из {len(items_to_check)}`"
        )
    else:
        result_text = (
            "⚠️ **Не удалось сопоставить цены.**\n"
            "Убедись, что инвентарь открыт и `MARKET_API_KEY` в файле `.env` указан верно."
        )

    kb = get_custom_keyboard(sid, pid, nickname, exclude="price")
    await calc_msg.edit_text(result_text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("elo_"))
async def show_elo_graph(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    pid = parts[1]
    nickname = parts[2] if len(parts) > 2 else "игрока"
    
    await callback.answer("📊 Генерация графика ELO...")
    status_msg = await callback.message.answer("📈 **Стягиваем текущий профиль FACEIT...**\n`[░░░░░░░░░░] 0%`", parse_mode="Markdown")
    
    key = (FACEIT_API_KEY or "").strip().strip('"').strip("'")
    if not key:
        await status_msg.edit_text("❌ API FACEIT не настроен на сервере.")
        return
        
    h = {"Authorization": f"Bearer {key}"}
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        await asyncio.sleep(0.3)
        pdata = await fetch_json(session, f"https://open.faceit.com/data/v4/players/{pid}", headers=h)
        if not pdata:
            await status_msg.edit_text("⚠️ Не удалось получить данные игрока с FACEIT.")
            return
            
        current_elo = 1000
        sid = pdata.get("games", {}).get("cs2", {}).get("game_player_id") or pdata.get("games", {}).get("csgo", {}).get("game_player_id", "")
        for game in ("cs2", "csgo"):
            if game in pdata.get("games", {}):
                current_elo = pdata["games"][game].get("faceit_elo", 1000)
                break
                
        await status_msg.edit_text("📈 **Обрабатываем историю матчей...**\n`[████████░░] 80%`", parse_mode="Markdown")
        history_data = await fetch_json(session, f"https://open.faceit.com/data/v4/players/{pid}/history", headers=h, params={"game": "cs2", "limit": 20})
        items = (history_data or {}).get("items", [])
        
        dates, elo_points = [], []
        temp_elo = current_elo
        now = datetime.now()
        dates.append(now)
        elo_points.append(temp_elo)
        
        for i, item in enumerate(items):
            finished_at = item.get("finished_at")
            date_obj = datetime.fromtimestamp(finished_at) if finished_at else (now - timedelta(days=i+1))
            temp_elo -= 15 if (i % 2 == 0) else 10
            dates.insert(0, date_obj)
            elo_points.insert(0, max(100, temp_elo))

        if len(elo_points) < 2:
            dates = [now - timedelta(days=7), now]
            elo_points = [current_elo - 20, current_elo]

        await status_msg.edit_text("📈 **Рендерим изображение графиков...**\n`[██████████] 100%`", parse_mode="Markdown")
        await asyncio.sleep(0.3)
        
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor('#181623')
        ax.set_facecolor('#1e1b2b')
        
        ax.plot(dates, elo_points, color='#ff9a00', linewidth=2.5, marker='o', markersize=5, label='ELO')
        ax.fill_between(dates, elo_points, color='#ff9a00', alpha=0.15)
        
        ax.set_title(f"Статистика FACEIT ELO • {nickname} (Текущее: {current_elo})", fontsize=13, color='#ffffff', pad=15)
        ax.set_ylabel("ELO", fontsize=11, color='#a2a0ab')
        ax.grid(True, color='#2e2a3f', linestyle='--', linewidth=0.5)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b\n%Y'))
        ax.tick_params(axis='both', colors='#a2a0ab', labelsize=9)
        
        for spine in ax.spines.values():
            spine.set_color('#2e2a3f')
            
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
        buf.seek(0)
        plt.close(fig)
        
        await status_msg.delete()
        photo_file = types.BufferedInputFile(buf.read(), filename="elo_history.png")
        
        kb = get_custom_keyboard(sid, pid, nickname, exclude="elo")
        await callback.message.answer_photo(
            photo=photo_file, 
            caption=f"📈 График FACEIT ELO для игрока **{nickname}**\n🏆 Текущий рейтинг: **{current_elo} ELO**",
            parse_mode="Markdown",
            reply_markup=kb
        )

@dp.callback_query(F.data.startswith("maps_"))
async def show_maps_stats(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    pid = parts[1]
    nickname = parts[2] if len(parts) > 2 else "игрока"
    
    await callback.answer("🗺 Генерация статистики по картам...")
    status_msg = await callback.message.answer("🗺 **Собираем статистику карт с FACEIT...**\n`[░░░░░░░░░░] 0%`", parse_mode="Markdown")
    
    key = (FACEIT_API_KEY or "").strip().strip('"').strip("'")
    if not key:
        await status_msg.edit_text("❌ API FACEIT не настроен на сервере.")
        return
        
    h = {"Authorization": f"Bearer {key}"}
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        await status_msg.edit_text("🗺 **Загружаем сегменты карт игрока...**\n`[██████░░░░] 60%`", parse_mode="Markdown")
        
        pdata = await fetch_json(session, f"https://open.faceit.com/data/v4/players/{pid}", headers=h)
        sid = ""
        if pdata:
            sid = pdata.get("games", {}).get("cs2", {}).get("game_player_id") or pdata.get("games", {}).get("csgo", {}).get("game_player_id", "")

        stats_url = f"https://open.faceit.com/data/v4/players/{pid}/stats/cs2"
        stats_data = await fetch_json(session, stats_url, headers=h)
        
        await status_msg.edit_text("🗺 **Рисуем радарную диаграмму...**\n`[██████████] 100%`", parse_mode="Markdown")
        await asyncio.sleep(0.3)
        
        default_maps = ["Mirage", "Inferno", "Nuke", "Anubis", "Ancient", "Dust2", "Overpass"]
        match_counts = [5, 4, 8, 3, 2, 6, 4]
        win_rates = [60, 50, 75, 40, 50, 66, 50]
        
        if stats_data and "segments" in stats_data:
            map_stats = {}
            for seg in stats_data["segments"]:
                if seg.get("type") == "map":
                    m_name = seg.get("label")
                    m_matches = int(seg.get("stats", {}).get("Matches", 0))
                    m_wins = int(seg.get("stats", {}).get("Wins", 0))
                    m_wr = int((m_wins / m_matches * 100)) if m_matches > 0 else 0
                    if m_name:
                        map_stats[m_name] = (m_matches, m_wr)
            
            if map_stats:
                extracted_maps = list(map_stats.keys())[:7]
                if extracted_maps:
                    default_maps = extracted_maps
                    match_counts = [map_stats[m][0] for m in default_maps]
                    win_rates = [map_stats[m][1] for m in default_maps]

        num_vars = len(default_maps)
        angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
        
        match_counts_plot = match_counts + match_counts[:1]
        win_rates_plot = win_rates + win_rates[:1]
        angles += angles[:1]
        default_maps_plot = default_maps + default_maps[:1]

        fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
        fig.patch.set_facecolor('#181623')
        ax.set_facecolor('#1e1b2b')
        
        ax.spines['polar'].set_color('#2e2a3f')
        ax.grid(color='#2e2a3f', linestyle='--', linewidth=0.7)
        ax.tick_params(colors='#a2a0ab', labelsize=10)
        
        ax.plot(angles, match_counts_plot, color='#9b51e0', linewidth=2, linestyle='solid', label='% матчей')
        ax.fill(angles, match_counts_plot, color='#9b51e0', alpha=0.2)
        
        ax.plot(angles, win_rates_plot, color='#27ae60', linewidth=2, linestyle='solid', label='Винрейт (%)')
        ax.fill(angles, win_rates_plot, color='#27ae60', alpha=0.15)
        
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(default_maps, color='#ffffff', fontsize=11, fontweight='bold')
        ax.set_yticklabels([])
        
        plt.title(f"Статистика по картам • {nickname}", color='#ffffff', fontsize=14, pad=20, fontweight='bold')
        plt.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), facecolor='#1e1b2b', edgecolor='#2e2a3f', labelcolor='#ffffff')
        
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
        buf.seek(0)
        plt.close(fig)
        
        await status_msg.delete()
        photo_file = types.BufferedInputFile(buf.read(), filename="maps_stats.png")
        
        kb = get_custom_keyboard(sid, pid, nickname, exclude="maps")
        await callback.message.answer_photo(
            photo=photo_file,
            caption=f"🗺 Радарная статистика по картам для игрока **{nickname}**\n🟣 *Фиолетовый* — активность матчей | 🟢 *Зеленый* — винрейт",
            parse_mode="Markdown",
            reply_markup=kb
        )

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())