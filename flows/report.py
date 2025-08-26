# -*- coding: utf-8 -*-
from __future__ import annotations
import os, pathlib, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
)
from keyboards import PLATFORM_LABEL, BTN_REPORT
from crud import (
    list_reportable_campaigns_for_user,
    get_campaign, get_user_admin, get_or_create_open_report, add_report_item,
    is_admin, is_superadmin, get_user_unit_id  
)
from datetime import datetime

from database import SessionLocal
from models import Report
from sqlalchemy import select
from models import ReportItem

from utils import safe_answer

REPORT_PICK_CAMPAIGN, REPORT_PICK_PLATFORM, REPORT_WAIT_PHOTOS = range(3)
DATA_DIR = pathlib.Path("storage").absolute()


from dotenv import load_dotenv

# بارگذاری فایل env
load_dotenv()

# خواندن متغیرها از env
ALLOWED_IMAGE_FORMATS = os.getenv("ALLOWED_IMAGE_FORMATS", "jpeg,png,webp").split(",")
ALLOWED_VIDEO_FORMATS = os.getenv("ALLOWED_VIDEO_FORMATS", "mp4,mov").split(",")
ALLOWED_DOCUMENT_FORMATS = os.getenv("ALLOWED_DOCUMENT_FORMATS", "pdf").split(",")
ALLOWED_ARCHIVE_FORMATS = os.getenv("ALLOWED_ARCHIVE_FORMATS", "zip").split(",")

MAX_IMAGE_SIZE = int(os.getenv("MAX_IMAGE_SIZE", 15))
MAX_VIDEO_SIZE = int(os.getenv("MAX_VIDEO_SIZE", 50))
MAX_DOCUMENT_SIZE = int(os.getenv("MAX_DOCUMENT_SIZE", 25))
MAX_ARCHIVE_SIZE = int(os.getenv("MAX_ARCHIVE_SIZE", 100))

DUPLICATE_POLICY = os.getenv("DUPLICATE_POLICY", "B")
BASE_STORAGE_PATH = os.getenv("BASE_STORAGE_PATH", "storage/{country_code}/c{cid}/{platform}/u{unit_id}")

DUPLICATE_SCOPE = os.getenv("DUPLICATE_SCOPE", "B")
MAX_FILES_PER_REPORT = int(os.getenv("MAX_FILES_PER_REPORT", 200))
MAX_FILES_PER_DAY = int(os.getenv("MAX_FILES_PER_DAY", 1000))




def is_valid_file(file_size, file_type):
    # بررسی فرمت
    if file_type.startswith("image"):
        allowed_formats = ALLOWED_IMAGE_FORMATS
        max_size = MAX_IMAGE_SIZE
    elif file_type.startswith("video"):
        allowed_formats = ALLOWED_VIDEO_FORMATS
        max_size = MAX_VIDEO_SIZE
    elif file_type == "application/pdf":
        allowed_formats = ALLOWED_DOCUMENT_FORMATS
        max_size = MAX_DOCUMENT_SIZE
    elif file_type == "application/zip":
        allowed_formats = ALLOWED_ARCHIVE_FORMATS
        max_size = MAX_ARCHIVE_SIZE
    else:
        return False, f"فرمت {file_type} مجاز نیست."

    # بررسی فرمت
    file_extension = file_type.split("/")[1].lower()  # به‌درستی از فرمت MIME استفاده کنیم
    if file_extension not in allowed_formats:
        return False, f"فرمت {file_extension} مجاز نیست."

    # بررسی حجم
    if file_size > max_size * 1024 * 1024:  # تبدیل MB به bytes
        return False, f"حجم فایل بیشتر از {max_size}MB است."

    return True, None


async def is_duplicate(session, report_id, file_name):
    # چک کردن فایل بر اساس نام فایل
    existing_item = await session.execute(
        select(ReportItem).where(ReportItem.report_id == report_id, ReportItem.file_name == file_name)
    )
    return existing_item.scalar() is not None

def get_storage_path(country_code, cid, platform, unit_id, report_id):
    path = BASE_STORAGE_PATH.format(
        country_code=country_code,
        cid=cid,
        platform=platform,
        unit_id=unit_id,
    )
    return pathlib.Path(path)

def campaigns_inline_keyboard(camps, manage: bool=False) -> InlineKeyboardMarkup:
    rows = []
    for c in camps:
        label = f"▫️ #{c.id} | {c.name} {'🟢' if c.active else '🔴'}"
        rows.append([InlineKeyboardButton(label, callback_data=f"camp:{c.id}")])
    if not rows:
        rows = [[InlineKeyboardButton("(خالی)", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_in_conversation"] = True
    uid = update.effective_user.id
    async with SessionLocal() as s:
        camps = await list_reportable_campaigns_for_user(s, uid, active_only=True)

    if not camps:
        async with SessionLocal() as s:
            if await is_admin(s, uid) or await is_superadmin(s, uid):
                await update.effective_message.reply_text("کمپین فعالی از والدِ واحد شما در دسترس نیست.")
            else:
                admin_id = await get_user_admin(s, uid)
                if admin_id is None:
                    await update.effective_message.reply_text("شما هنوز به واحدی متصل نشده‌اید.")
                else:
                    await update.effective_message.reply_text("کمپین فعالی از واحدِ بالادست وجود ندارد.")
        return ConversationHandler.END

    context.user_data["report_cids"] = {c.id for c in camps}
    await update.effective_message.reply_text("کمپین را انتخاب کنید:", reply_markup=campaigns_inline_keyboard(camps))
    return REPORT_PICK_CAMPAIGN

async def report_pick_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)
    if not q.data.startswith("camp:"):
        return REPORT_PICK_CAMPAIGN

    cid = int(q.data.split(":")[1])
    allowed = context.user_data.get("report_cids") or set()
    if cid not in allowed:
        await q.edit_message_text("⛔️ دسترسی به این کمپین ندارید.")
        return ConversationHandler.END

    # کمپین را می‌خوانیم تا پلتفرم‌ها را نشان دهیم
    async with SessionLocal() as s:
        camp = await get_campaign(s, cid)
    if not camp or not camp.active:
        await q.edit_message_text("کمپین نامعتبر/غیرفعال است.")
        return ConversationHandler.END

    context.user_data["report_campaign_id"] = cid
    plats = __import__("json").loads(camp.platforms)
    rows = [[InlineKeyboardButton(PLATFORM_LABEL.get(p, p), callback_data=f"rpf:{p}")] for p in plats]
    await q.edit_message_text("پلتفرم هدف را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(rows))
    return REPORT_PICK_PLATFORM

async def report_pick_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)
    if not q.data.startswith("rpf:"):
        return REPORT_PICK_PLATFORM
    platform = q.data.split(":")[1]
    context.user_data["report_platform"] = platform
    await q.edit_message_text(
        f"پلتفرم: {PLATFORM_LABEL.get(platform, platform)}\n"
        f"عکس‌ها را بفرستید. هر تعداد. وقتی تمام شد /done را بزنید."
    )
    return REPORT_WAIT_PHOTOS

async def receive_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = context.user_data.get("report_campaign_id")
    platform = context.user_data.get("report_platform")
    if not cid or not platform:
        return

    photos = update.message.photo
    if not photos:
        return
    best = photos[-1]

    uid = update.effective_user.id
    allowed = context.user_data.get("report_cids") or set()
    if cid not in allowed:
        await update.message.reply_text("⛔️ اجازه ثبت برای این کمپین را ندارید.")
        return

    # فایل تلگرام
    tg_file = await update.get_bot().get_file(best.file_id)
    file_name = tg_file.file_path.split("/")[-1]  # مثلا "file_28.jpg"

    async with SessionLocal() as s:
        # کمپین معتبر؟
        camp = await get_campaign(s, cid)
        if not camp or not camp.active:
            await update.message.reply_text("کمپین نامعتبر/غیرفعال است.")
            return

        # واحد کاربر
        current_unit_id = await get_user_unit_id(s, uid)
        if not current_unit_id:
            await update.message.reply_text("⛔️ شما به هیچ واحدی متصل نیستید.")
            return

        # گزارش باز را بگیر/بساز
        report_id = await get_or_create_open_report(s, uid, cid, platform, None)
        rep = await s.get(Report, report_id)
        rep.unit_id_owner = current_unit_id
        rep.platform = platform

        # چک تکراری داخل همین گزارش، بر اساس نام فایل
        exists = await s.execute(
            select(ReportItem.id)
            .join(Report, ReportItem.report_id == Report.id)
            .where(
                Report.campaign_id == cid,
                Report.unit_id_owner == current_unit_id,
                ReportItem.file_name == file_name,   # همان نام فایل
            )
            .limit(1)
        )
        if exists.scalar() is not None:
            await update.message.reply_text(f"⚠️ این فایل قبلاً در همین کمپین/واحد (صرف‌نظر از پلتفرم) ثبت شده: {file_name}")
            return


        # مسیر نهایی
        base_dir = get_storage_path("ir", cid, platform, current_unit_id, report_id)
        base_dir.mkdir(parents=True, exist_ok=True)
        final_path = base_dir / file_name

        # دانلود و ثبت
        await tg_file.download_to_drive(final_path)
        await add_report_item(s, report_id, best.file_id, str(final_path), platform, file_name)
        await s.commit()

    await update.message.reply_text("✅ ذخیره شد. عکس بعدی را بفرستید یا /done را بزنید.")

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = context.user_data.pop("report_campaign_id", None)
    context.user_data.pop("report_platform", None)
    context.user_data.pop("report_cids", None)
    context.user_data.pop("_in_conversation", None)
    if cid:
        await update.message.reply_text("گزارش شما ثبت شد. می‌توانید دوباره /report بزنید.")
    return ConversationHandler.END

def build_report_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler("report", report_cmd),
            MessageHandler(filters.Regex(r"^📤 ارسال گزارش$"), report_cmd),
            # 👇 مهم: دکمه‌های داشبورد ادمین/سوپرادمین
            CallbackQueryHandler(report_cmd, pattern=r"^(sa|adm):report:submit$"),
        ],
        states={
            REPORT_PICK_CAMPAIGN:  [CallbackQueryHandler(report_pick_campaign, pattern=r"^camp:\d+$")],
            REPORT_PICK_PLATFORM:  [CallbackQueryHandler(report_pick_platform, pattern=r"^rpf:.+$")],
            REPORT_WAIT_PHOTOS:    [MessageHandler(filters.PHOTO, receive_photos)],
        },
        fallbacks=[CommandHandler("done", done)],
        allow_reentry=True,
    )
