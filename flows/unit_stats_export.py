# -*- coding: utf-8 -*-
from __future__ import annotations
import os, zipfile, pathlib
from typing import Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes
from database import SessionLocal
from keyboards import UNIT_TYPE_LABELS, PLATFORM_LABEL
from crud import (
    is_superadmin, list_units_for_actor,
    list_campaigns_reported_by_unit, stats_for_unit_campaign, stats_for_unit_all_campaigns,
    fetch_unit_campaign_items, fetch_unit_all_items, get_campaign
)

DATA_DIR = pathlib.Path("storage").absolute()

def _unit_pick_keyboard(units, base: str) -> InlineKeyboardMarkup:
    rows = []
    for u in units:
        label = f"{UNIT_TYPE_LABELS.get(u.type,u.type)} {u.name} (#{u.id})"
        rows.append([InlineKeyboardButton(label, callback_data=f"{base}:pickunit:{u.id}")])
    if not rows:
        rows = [[InlineKeyboardButton("(خالی)", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)

def _campaigns_keyboard(campaigns, base: str, unit_id: int, include_all: bool, all_caption: str) -> InlineKeyboardMarkup:
    rows = []
    if include_all:
        rows.append([InlineKeyboardButton(all_caption, callback_data=f"{base}:all:{unit_id}")])
    for c in campaigns:
        label = f"#{c.id} | {c.name} {'🟢' if c.active else '🔴'}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{base}:camp:{unit_id}:{c.id}")])
    if not rows:
        rows = [[InlineKeyboardButton("(کمپینی که این واحد گزارش داده ندارد)", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)

def _fa_platform_dir(platform: str) -> str:
    if not platform or platform == "unknown":
        return "نامشخص"
    label = PLATFORM_LABEL.get(platform, platform)
    for junk in ("◽️ ", "✅ ", "▫️ ", "• "):
        if label.startswith(junk):
            label = label[len(junk):]
    return label.replace("/", "／").strip() or "نامشخص"

async def _send_stats_for_unit_campaign(q, unit_id: int, campaign_id: int):
    async with SessionLocal() as s:
        rows = await stats_for_unit_campaign(s, unit_id, campaign_id)
        camp = await get_campaign(s, campaign_id)
    if not camp:
        return await q.edit_message_text("کمپین یافت نشد.")
    if not rows:
        return await q.edit_message_text(f"برای این واحد در کمپین #{campaign_id} گزارشی ثبت نشده.")
    lines = [f"📊 آمار واحد #{unit_id} در کمپین #{campaign_id} — {camp.name}"]
    for plat, cnt in rows:
        lines.append(f"• {PLATFORM_LABEL.get(plat, plat)}: {cnt}")
    await q.edit_message_text("\n".join(lines))

async def _send_stats_for_unit_all(q, unit_id: int):
    async with SessionLocal() as s:
        rows = await stats_for_unit_all_campaigns(s, unit_id)
    if not rows:
        return await q.edit_message_text("برای این واحد گزارشی ثبت نشده.")
    # گروه‌بندی بر اساس کمپین
    by_camp = {}
    for r in rows:
        key = (r["campaign_id"], r["campaign_name"])
        by_camp.setdefault(key, []).append((r["platform"], r["count"]))
    lines = [f"📊 آمار کلی واحد #{unit_id} روی همهٔ کمپین‌هایش:"]
    for (cid, cname), items in by_camp.items():
        lines.append(f"\n#{cid} — {cname}:")
        for plat, cnt in items:
            lines.append(f"• {PLATFORM_LABEL.get(plat, plat)}: {cnt}")
    await q.edit_message_text("\n".join(lines))

async def _export_zip_unit_campaign(unit_id: int, campaign_id: int) -> Optional[str]:
    async with SessionLocal() as s:
        items = await fetch_unit_campaign_items(s, unit_id, campaign_id)
        camp = await get_campaign(s, campaign_id)
    if not items or not camp:
        return None
    zip_name = DATA_DIR / f"unit_{unit_id}__campaign_{campaign_id}.zip"
    if zip_name.exists(): zip_name.unlink()
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, platform, user_id, cid, cname in items:
            if not file_path or not os.path.exists(file_path):
                continue
            plat_dir = _fa_platform_dir(platform)
            filename = os.path.basename(file_path)
            arcname = f"{plat_dir}/user_{user_id}__{filename}"
            zf.write(file_path, arcname=arcname)
    return str(zip_name)

async def _export_zip_unit_all(unit_id: int) -> Optional[str]:
    async with SessionLocal() as s:
        items = await fetch_unit_all_items(s, unit_id)
    if not items:
        return None
    zip_name = DATA_DIR / f"unit_{unit_id}__all_campaigns.zip"
    if zip_name.exists(): zip_name.unlink()
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, platform, user_id, cid, cname in items:
            if not file_path or not os.path.exists(file_path):
                continue
            plat_dir = _fa_platform_dir(platform)
            safe_camp_dir = f"کمپین #{cid} - {cname}".replace("/", "／")
            filename = os.path.basename(file_path)
            arcname = f"{safe_camp_dir}/{plat_dir}/user_{user_id}__{filename}"
            zf.write(file_path, arcname=arcname)
    return str(zip_name)

async def unit_stats_export_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر یک‌پارچه برای:
       sa:unit:stats | adm:unit:stats | sa:unit:export | adm:unit:export
       مراحل: انتخاب واحد ← انتخاب کمپین/یا همه ← نمایش آمار/ارسال ZIP
    """
    q = update.callback_query
    data = q.data  # مثال‌ها: sa:unit:stats  | sa:unit:stats:pickunit:12 | sa:unit:stats:camp:12:5 | sa:unit:export:all:12
    parts = data.split(":")
    role = parts[0]   # sa | adm
    feature = parts[2]  # stats | export

    # مرحله 1: انتخاب واحد (اگر نیاز باشد)
    if len(parts) == 3:
        async with SessionLocal() as s:
            units = await list_units_for_actor(s, q.from_user.id)
        if not units:
            return await q.edit_message_text("هیچ واحدی برای شما در دسترس نیست.")
        # اگر فقط یک واحد دارد، میان‌بر:
        if len(units) == 1:
            unit_id = units[0].id
            # برو مرحلهٔ انتخاب کمپین/گزینه‌ها
            async with SessionLocal() as s:
                camps = await list_campaigns_reported_by_unit(s, unit_id, active_only=False)
            if feature == "stats":
                kb = _campaigns_keyboard(camps, f"{role}:unit:stats", unit_id, True, "📊 مجموع همهٔ کمپین‌ها")
                return await q.edit_message_text(f"واحد انتخاب‌شده: {units[0].name}\nیک کمپین انتخاب کنید:", reply_markup=kb)
            else:
                kb = _campaigns_keyboard(camps, f"{role}:unit:export", unit_id, True, "🗂️ خروجی همهٔ کمپین‌ها")
                return await q.edit_message_text(f"واحد انتخاب‌شده: {units[0].name}\nیک کمپین انتخاب کنید:", reply_markup=kb)
        # در غیر این صورت لیست انتخاب واحد
        kb = _unit_pick_keyboard(units, f"{role}:unit:{feature}")
        return await q.edit_message_text("واحد را انتخاب کنید:", reply_markup=kb)

    # مرحله 2:  انتخاب کمپین    
    if len(parts) >= 4 and parts[3] == "pickunit":
        unit_id = int(parts[4])
        async with SessionLocal() as s:
            camps = await list_campaigns_reported_by_unit(s, unit_id, active_only=False)
        if feature == "stats":
            kb = _campaigns_keyboard(camps, f"{role}:unit:stats", unit_id, True, "📊 مجموع همهٔ کمپین‌ها")
            return await q.edit_message_text("یک کمپین انتخاب کنید:", reply_markup=kb)
        else:
            kb = _campaigns_keyboard(camps, f"{role}:unit:export", unit_id, True, "🗂️ خروجی همهٔ کمپین‌ها")
            return await q.edit_message_text("یک کمپین انتخاب کنید:", reply_markup=kb)

    # مرحله 3: عمل روی انتخاب کمپین/یا همه
    if feature == "stats":
        if len(parts) >= 6 and parts[3] == "camp":
            unit_id = int(parts[4])
            campaign_id = int(parts[5])
            return await _send_stats_for_unit_campaign(q, unit_id, campaign_id)
        if len(parts) >= 5 and parts[3] == "all":
            unit_id = int(parts[4])
            return await _send_stats_for_unit_all(q, unit_id)

    if feature == "export":
        if len(parts) >= 6 and parts[3] == "camp":
            unit_id = int(parts[4])
            campaign_id = int(parts[5])
            zippath = await _export_zip_unit_campaign(unit_id, campaign_id)
            if not zippath:
                return await q.edit_message_text("برای این واحد در این کمپین فایلی یافت نشد.")
            await q.message.reply_document(InputFile(open(zippath, "rb"), filename=os.path.basename(zippath)))
            return
        if len(parts) >= 5 and parts[3] == "all":
            unit_id = int(parts[4])
            zippath = await _export_zip_unit_all(unit_id)
            if not zippath:
                return await q.edit_message_text("برای این واحد فایلی یافت نشد.")
            await q.message.reply_document(InputFile(open(zippath, "rb"), filename=os.path.basename(zippath)))
            return

    # پیش‌فرض
    await q.answer()
