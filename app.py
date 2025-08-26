# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import pathlib
import asyncio
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from flows.common import cancel_cmd
from flows.units import build_unit_wizard_conversation
from flows.units import build_unit_attach_conversation
from flows.units import build_unit_admins_manager_conversation


# --- Flows ---
from flows.report import build_report_conversation
from flows.manage import campaigns_cmd as manage_campaigns_cmd, manage_cb, edit_text_receiver,edit_platforms_toggle,campaigns_search_receiver,campaigns_browse_cb
from flows.units import unit_add_cmd, unit_list_cmd, unit_attach_cmd
from flows.admin import addadmin_cmd, linkadmin_cmd, myusers_cmd, adduser_cmd, renameuser_cmd, deluser_cmd
from flows.user_shortcuts import mystats_cmd, myexport_cmd, user_cb
from flows.newcampaign import newcampaign, build_conversation as build_newcamp_conversation
from flows.units import build_unit_list_conversation, ul_start
from flows.unit_stats_export import unit_stats_export_cb
from flows.admin_profile import profile_cmd, profile_cb



# --- Core ---
from database import init_db, SessionLocal
from models import Admin
from keyboards import user_reply_kb, admin_reply_kb, superadmin_reply_kb
from crud import is_admin, is_superadmin, get_user_admin, list_campaigns_for_admin_units
from flows.superadmin import dashboard_entry, sa_router, adm_router
from keyboards import BTN_SA_DASH
from re import escape as re_escape
import json, re


# --- Storage dir ---
DATA_DIR = pathlib.Path("storage").absolute()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# سوپرادمین‌های اولیه (می‌تونی از .env هم بخونی)
# سوپرادمین‌های اولیه (از .env)

async def bootstrap_admins(hard_admins: set[int]):
    async with SessionLocal() as s:
        for aid in hard_admins:
            if not await s.get(Admin, aid):
                s.add(Admin(admin_id=aid, role="SUPER"))
        await s.commit()

# ------------------ Handlers ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        uid = update.effective_user.id
        if await is_admin(s, uid):
            text = "سلام!\n\nاز دکمه‌ها استفاده کن 👇"
            kb = superadmin_reply_kb() if await is_superadmin(s, uid) else admin_reply_kb()
            await update.effective_message.reply_text(text, reply_markup=kb)
        else:
            text = "سلام!\n\nبرای ارسال گزارش دکمه زیر را بزن."
            if await get_user_admin(s, uid) is None:
                text += "\n(برای دسترسی به کمپین‌ها باید ادمین شما را اضافه کند.)"
            await update.effective_message.reply_text(text, reply_markup=user_reply_kb())

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        kb = superadmin_reply_kb() if await is_superadmin(s, update.effective_user.id) else admin_reply_kb()
    await update.effective_message.reply_text(
        "پنل ادمین فعال است. از دکمه‌ها/دستورات استفاده کنید:",
        reply_markup=kb
    )

async def campaigns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        uid = update.effective_user.id
        if not await is_admin(s, uid):
            return
        camps = await list_campaigns_for_admin_units(s, uid, active_only=False)

    rows = []
    for c in camps:
        label = f"▫️ #{c.id} | {c.name} {'🟢' if c.active else '🔴'}"
        rows.append([InlineKeyboardButton(label, callback_data=f"camp:{c.id}:manage")])
    if not rows:
        rows = [[InlineKeyboardButton("(خالی)", callback_data="noop")]]

    await update.effective_message.reply_text(
        "کمپین‌های شما:",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    
def parse_int_set_env(var_name: str, default: str = "") -> set[int]:
    raw = os.getenv(var_name, default).strip()
    if not raw:
        return set()
    # تلاش برای JSON
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return {int(x) for x in val if str(x).strip()}
        if isinstance(val, int):
            return {val}
    except Exception:
        pass
    # جداکننده‌های کاما/فاصله/خط
    parts = re.split(r"[,\s]+", raw)
    out = set()
    for p in parts:
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            pass
    return out

# ------------------ App wiring ------------------
def main():
    load_dotenv()
    
    hard_admins = parse_int_set_env("HARD_ADMINS")
    asyncio.run(init_db())
    asyncio.run(bootstrap_admins(hard_admins))

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        print("⚠️ TELEGRAM_TOKEN را در .env تنظیم کنید.")
        return

    import logging
    logging.basicConfig(level=logging.INFO)

    app: Application = ApplicationBuilder().token(token).build()

    # 1) هندلر مربوط به گزارش رو **قبل از** سایر هندلرها قرار بده
    app.add_handler(build_report_conversation())  # قرار دادن ConversationHandler اول

    # 2) هندلر برای ویرایش‌های داخل فرم‌ها (نابلاک)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, edit_text_receiver, block=False), group=-1)

    # 3) عمومی
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_cmd), group=-2)
    app.add_handler(CommandHandler("admin", admin_panel))

    # 4) مدیریت ادمین/کاربر
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("linkadmin", linkadmin_cmd))
    app.add_handler(CommandHandler("myusers", myusers_cmd))
    app.add_handler(CommandHandler("adduser", adduser_cmd))
    app.add_handler(CommandHandler("renameuser", renameuser_cmd))
    app.add_handler(CommandHandler("deluser", deluser_cmd))

    # 5) واحدها — CLIهای صریح
    app.add_handler(CommandHandler("unit_add", unit_add_cmd))     # CLI (سریع)
    app.add_handler(CommandHandler("unit_list", unit_list_cmd))   # CLI
    app.add_handler(CommandHandler("unit_attach", unit_attach_cmd))# CLI

    # 6) واحدها — مکالمه‌ها (ویزاردها/مرور)
    app.add_handler(build_unit_wizard_conversation())          # /unit_add_wiz + (sa|adm):unit:add
    app.add_handler(build_unit_attach_conversation())          # /unit_attach_wizard + (sa|adm):unit:attach
    app.add_handler(build_unit_list_conversation())            # /unit_list + (sa|adm):unit:list
    app.add_handler(build_unit_admins_manager_conversation())  # /unit_admins + (sa|adm):unit:admins

    # 7) کمپین‌ها
    app.add_handler(CommandHandler("campaigns", manage_campaigns_cmd))  # ← به‌جای campaigns_cmd محلی
    app.add_handler(CallbackQueryHandler(edit_platforms_toggle, pattern=r"^epf:"))
    app.add_handler(CallbackQueryHandler(
        manage_cb,
        pattern=r"^(camp:\d+:manage|edit:\d+:[a-z_]+|(?:toggle|delete|delok|stats|export):\d+)$"
    ))

    # نمای فیلتردار کمپین‌ها (callbackهای cl:...)
    app.add_handler(CallbackQueryHandler(campaigns_browse_cb, pattern=r"^cl:"))

    # دریافت متن جستجو برای نمای فیلتردار
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, campaigns_search_receiver, block=False),
        group=10
    )

    # 8) گزارش کاربر و خروجی
    app.add_handler(CommandHandler("mystats", mystats_cmd))
    app.add_handler(CommandHandler("myzip", myexport_cmd))
    app.add_handler(CallbackQueryHandler(user_cb, pattern=r"^(ustats:\d+|uexport:\d+)$"))

    # 9) ساخت کمپین (Conversation)
    app.add_handler(build_newcamp_conversation())

    # 10) داشبورد سوپرادمین
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(rf"^{re_escape(BTN_SA_DASH)}$"),
            dashboard_entry
        ),
        group=0,
    )
    app.add_handler(
        MessageHandler(filters.Regex(r"(?i)^(لغو|cancel)$") & ~filters.COMMAND, cancel_cmd),
        group=-2
    )
    
    app.add_handler(CallbackQueryHandler(
        unit_stats_export_cb,
        pattern=r"^(sa|adm):unit:(stats|export)(?::.*)?$"
    ))
    
    # فرمان مستقیم
    app.add_handler(CommandHandler("profile", profile_cmd))

    # دکمه‌های داشبورد
    app.add_handler(CallbackQueryHandler(profile_cb, pattern=r"^(sa|adm):profile(?::.*)?$"))


    


    # ⚠️ مهم: روترهای کلی در انتهای لیست؛ بعد از مکالمه‌ها
    app.add_handler(CallbackQueryHandler(sa_router, pattern=r"^sa:"))
    app.add_handler(CallbackQueryHandler(adm_router, pattern=r"^adm:"))

    # 11) event loop (Py 3.12)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    print("🚀 Bot is running (ORM-ready)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()