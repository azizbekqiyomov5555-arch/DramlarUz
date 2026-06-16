# -*- coding: utf-8 -*-
"""
Auto-tolov + Mini App integratsiyali Telegram bot (namuna).
- Asosiy bot tokenini BOT_TOKEN env-ga qo'ying.
- MINI_APP_URL — GitHub Pages'dagi index.html manzili.
- MAIN_ADMIN_ID — pul yechish so'rovlari yuboriladigan asosiy admin (sizning ID: 8723400610).
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from urllib.parse import urlencode

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8723400610:AAEFHdluEW7eZh2vnRHgCFbUrSjL3K3BAJ0")
MINI_APP_URL  = os.environ.get("MINI_APP_URL", "https://USERNAME.github.io/REPO/index.html")
MAIN_ADMIN_ID = int(os.environ.get("MAIN_ADMIN_ID", "8723400610"))  # Asosiy bot admini

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# ---- Demo "DB" (haqiqiy loyihada PostgreSQL/Mongo ishlatiladi) ----
USERS = {}   # uid -> {"balance": int, "card": str, "card_name": str}
MYBOTS = {}  # uid -> [ {id, bot_username, bot_token, owner_id, tarif, status, created_at, bot_type} ]


def user(uid: int) -> dict:
    return USERS.setdefault(uid, {"balance": 25000, "card": "5614681872672690", "card_name": "Qiyomov Azizbek"})


def fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


# ---------- /start ----------
@dp.message(CommandStart())
async def start(m: Message):
    user(m.from_user.id)
    # demo bot ro'yxati
    MYBOTS.setdefault(m.from_user.id, [{
        "id": 1,
        "bot_username": "MyKinoBot",
        "bot_token": "1234567:AA...",
        "owner_id": m.from_user.id,
        "tarif": "Premium",
        "status": "active",
        "created_at": datetime.utcnow(),
        "bot_type": "Kino Bot",
        "exp_at": datetime.utcnow() + timedelta(days=30),
    }])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Mening botlarim", callback_data="mybots")],
        [InlineKeyboardButton(text="💰 Balans / Pul yechish", callback_data="balance")],
    ])
    await m.answer("👋 Salom! Botingizni boshqarish uchun tugmani tanlang:", reply_markup=kb)


# ---------- Mening botlarim ----------
@dp.callback_query(F.data == "mybots")
async def mybots(c: CallbackQuery):
    uid = c.from_user.id
    bots = MYBOTS.get(uid, [])
    if not bots:
        return await c.message.edit_text("Sizda hali bot yo'q.")

    rows = []
    for b in bots:
        exp = b.get("exp_at")
        exp_txt = exp.strftime("%Y-%m-%d") if exp else "—"
        params = {
            "bot_id":   b["id"],
            "name":     b["bot_username"],
            "username": b["bot_username"],
            "token":    b.get("bot_token", ""),
            "admin":    b.get("owner_id", uid),
            "status":   b.get("status", "active"),
            "tarif":    b.get("tarif", "Free"),
            "exp":      exp_txt,
            "created":  b["created_at"].strftime("%Y-%m-%d %H:%M"),
            "type":     b.get("bot_type", "Kino Bot"),
        }
        sep = "&" if "?" in MINI_APP_URL else "?"
        web_url = f"{MINI_APP_URL}{sep}{urlencode(params)}"
        rows.append([InlineKeyboardButton(
            text=f"🤖 {b['bot_username']}",
            web_app=WebAppInfo(url=web_url),
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back")])
    await c.message.edit_text("🤖 <b>Mening botlarim:</b>\nMini ilovani ochish uchun botni tanlang.",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "back")
async def back(c: CallbackQuery):
    await start(c.message)


# ---------- Balans / Pul yechish ----------
@dp.callback_query(F.data == "balance")
async def balance(c: CallbackQuery):
    u = user(c.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pul yechish", callback_data="withdraw")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back")],
    ])
    await c.message.edit_text(
        f"💰 <b>Balans:</b> {fmt(u['balance'])} so'm\n"
        f"💳 Karta: <code>{u['card']}</code>\n"
        f"👤 Egasi: {u['card_name']}",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "withdraw")
async def withdraw(c: CallbackQuery):
    uid = c.from_user.id
    u = user(uid)
    amount = u["balance"]
    if amount < 1000:
        return await c.answer("Yetarli balans yo'q", show_alert=True)

    commission = int(amount * 0.05)
    net = amount - commission
    u["balance"] = 0  # demo

    # Foydalanuvchiga javob
    await c.message.edit_text(
        "✅ <b>So'rov yuborildi!</b>\n\n"
        f"💵 Yechilmoqda: {fmt(amount)} so'm\n"
        f"💳 Karta: <code>{u['card']}</code>\n"
        f"👤 Karta egasi: {u['card_name']}\n"
        f"📉 Komissiya (5%): {fmt(commission)} so'm\n"
        f"💰 Kartaga tushadi: {fmt(net)} so'm\n\n"
        f"📊 Yangi balans: 0 so'm\n\n"
        "⏳ Tez orada amalga oshiriladi."
    )

    # ✅ ASOSIY BOT ADMINIGA YUBORISH
    try:
        await bot.send_message(
            MAIN_ADMIN_ID,
            "🔔 <b>YANGI PUL YECHISH SO'ROVI</b>\n\n"
            f"👤 Foydalanuvchi: <a href='tg://user?id={uid}'>{c.from_user.full_name}</a>\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📛 Username: @{c.from_user.username or '—'}\n\n"
            f"💵 Miqdor: {fmt(amount)} so'm\n"
            f"💳 Karta: <code>{u['card']}</code>\n"
            f"👤 Karta egasi: {u['card_name']}\n"
            f"📉 Komissiya (5%): {fmt(commission)} so'm\n"
            f"💰 Kartaga tushadi: {fmt(net)} so'm\n\n"
            f"🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        )
    except Exception as e:
        logging.exception("MAIN admin xabar yuborilmadi: %s", e)


# ---------- Mini App'dan keladigan ma'lumotlar ----------
@dp.message(F.web_app_data)
async def web_app_data(m: Message):
    try:
        data = json.loads(m.web_app_data.data)
    except Exception:
        return await m.answer("❌ Noto'g'ri ma'lumot.")

    action = data.get("action")
    bot_id = data.get("bot_id")
    bots = MYBOTS.get(m.from_user.id, [])
    target = next((b for b in bots if str(b["id"]) == str(bot_id)), None)

    if action == "update_settings" and target:
        if data.get("token"): target["bot_token"] = data["token"]
        if data.get("admin"): target["owner_id"]  = int(data["admin"])
        if data.get("name"):  target["bot_username"] = data["name"]
        return await m.answer("✅ Sozlamalar saqlandi.")

    if action == "delete" and target:
        bots.remove(target)
        return await m.answer(f"🗑 {target['bot_username']} o'chirildi.")

    if action == "extend" and target:
        target["exp_at"] = (target.get("exp_at") or datetime.utcnow()) + timedelta(days=30)
        return await m.answer(f"💎 {target['bot_username']} muddati 30 kunga uzaytirildi.")

    await m.answer(f"ℹ️ Qabul qilindi: {action}")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
