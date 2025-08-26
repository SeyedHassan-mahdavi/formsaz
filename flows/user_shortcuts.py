# -*- coding: utf-8 -*-
from __future__ import annotations
import os, zipfile, pathlib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from database import SessionLocal
from crud import list_campaigns_for_user, get_campaign, stats_for_user_campaign, get_user_admin
from keyboards import PLATFORM_LABEL

DATA_DIR = pathlib.Path("storage").absolute()

def user_campaigns_keyboard(campaigns, purpose: str) -> InlineKeyboardMarkup:
    rows = []
    for c in campaigns:
        label = f"#{c.id} | {c.name} {'🟢' if c.active else '🔴'}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{purpose}:{c.id}")])
    if not rows:
        rows = [[InlineKeyboardButton("(خالی)", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)

async def mystats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    async with SessionLocal() as s:
        camps = await list_campaigns_for_user(s, uid, active_only=False)
    if not camps:
        return await update.effective_message.reply_text("هیچ کمپینی برای شما یافت نشد.")
    await update.effective_message.reply_text("کمپین را برای مشاهدهٔ آمار خود انتخاب کنید:",
                                              reply_markup=user_campaigns_keyboard(camps, "ustats"))

async def myexport_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    async with SessionLocal() as s:
        camps = await list_campaigns_for_user(s, uid, active_only=False)
    if not camps:
        return await update.effective_message.reply_text("هیچ کمپینی برای شما یافت نشد.")
    await update.effective_message.reply_text("کمپین را برای دریافت خروجی ZIP فایل‌های خود انتخاب کنید:",
                                              reply_markup=user_campaigns_keyboard(camps, "uexport"))

async def user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data
    uid = q.from_user.id
    async with SessionLocal() as s:
        if data.startswith("ustats:"):
            cid = int(data.split(":")[1])
            camp = await get_campaign(s, cid)
            if not camp or (await get_user_admin(s, uid)) != camp.admin_id:
                return await q.edit_message_text("اجازه ندارید.")
            rows = await stats_for_user_campaign(s, cid, uid)
            if not rows:
                return await q.edit_message_text(f"برای کمپین #{cid} هنوز گزارشی ثبت نکرده‌اید.")
            lines = [f"📊 آمار شما در کمپین #{cid}:"]
            for plat, cnt in rows:
                lines.append(f"• {PLATFORM_LABEL.get(plat, plat)}: {cnt}")
            return await q.edit_message_text("\n".join(lines))
        if data.startswith("uexport:"):
            cid = int(data.split(":")[1])
            camp = await get_campaign(s, cid)
            if not camp or (await get_user_admin(s, uid)) != camp.admin_id:
                return await q.edit_message_text("اجازه ندارید.")
    zippath = await export_zip_user(cid, uid)
    if not zippath:
        return await q.edit_message_text("برای این کمپین فایلی از شما یافت نشد.")
    await q.message.reply_document(InputFile(open(zippath, 'rb'), filename=os.path.basename(zippath)))

async def export_zip_user(campaign_id: int, user_id: int):
    base = DATA_DIR / f"campaign_{campaign_id}"
    if not base.exists(): return None
    files = []
    for file in base.rglob("*"):
        if file.is_file():
            rel = file.relative_to(base); parts = rel.parts
            if len(parts) >= 3 and parts[1] == f"user_{user_id}":
                platform = parts[0]; files.append((str(file), platform))
    if not files: return None
    zip_name = DATA_DIR / f"campaign_{campaign_id}_user_{user_id}.zip"
    if zip_name.exists(): zip_name.unlink()
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path, platform in files:
            filename = os.path.basename(file_path)
            arcname = f"{platform}/{filename}"
            zf.write(file_path, arcname=arcname)
    return str(zip_name)
