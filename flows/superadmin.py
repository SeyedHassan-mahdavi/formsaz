# flows/superadmin.py
from telegram import Update
from telegram.ext import ContextTypes
from database import SessionLocal
from crud import is_admin, is_superadmin, get_primary_unit_for_admin  # ← اضافه شد
from keyboards import (
    sa_main_menu, sa_units_menu, sa_admins_menu, sa_campaigns_menu, sa_reports_menu, sa_back_home,
    adm_main_menu, adm_units_menu, adm_campaigns_menu,  
)
from flows.units import ul_start  # برای باز کردن مرور واحدها

from telegram.constants import ParseMode
from keyboards import campaigns_inline_keyboard 
from .manage import campaigns_cmd  
from flows.report import report_cmd 

async def dashboard_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    async with SessionLocal() as s:
        if await is_superadmin(s, uid):
            return await update.effective_message.reply_text("داشبورد مدیریت:", reply_markup=sa_main_menu())
        if await is_admin(s, uid):
            return await update.effective_message.reply_text("داشبورد مدیریت (ادمین):", reply_markup=adm_main_menu())
    return await update.effective_message.reply_text("⛔️ فقط ادمین‌ها و سوپرادمین‌ها دسترسی دارند.")


async def adm_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    uid = q.from_user.id
    async with SessionLocal() as s:
        if not await is_admin(s, uid):
            return await q.answer("⛔️ فقط ادمین.", show_alert=True)

    data = q.data

    # بازگشت به خانه
    if data in ("adm", "adm:home"):
        return await q.edit_message_text("داشبورد مدیریت (ادمین):", reply_markup=adm_main_menu())

    # ===== کمپین‌ها =====
    if data == "adm:camp":
        return await q.edit_message_text("کمپین‌ها:", reply_markup=adm_campaigns_menu())

    if data == "adm:camp:list":
        # همان لیست کمپین‌ها؛ خودش فقط حوزهٔ ادمین را می‌آورد
        await campaigns_cmd(update, context)
        return

    # ===== واحدها =====
    if data == "adm:unit":
        return await q.edit_message_text("مدیریت واحدها:", reply_markup=adm_units_menu())

    if data == "adm:unit:list":
        # پیشنهاد محدودسازی: فقط زیر درخت واحدِ اصلی ادمین را نشان بده
        return await ul_start(update, context)  # ← اگر ul_start فعلاً کل را نشان می‌دهد، بند 3 را ببین

    if data == "adm:unit:add":
        # اگر اجازهٔ ساخت در حوزه دارد: ویزارد ساخت را با دستور راهنمایی کن
        return await q.edit_message_text("برای ساخت واحد از دستور `/unit_add` استفاده کنید.",
                                         parse_mode="Markdown", reply_markup=adm_units_menu())

    if data == "adm:unit:attach":
        return await q.edit_message_text("برای اتصال ادمین به واحد از دستور `/unit_attach` استفاده کنید.",
                                         parse_mode="Markdown", reply_markup=adm_units_menu())

    # ===== گزارش‌ها =====
    if data == "adm:report":
        return await q.edit_message_text(
            "گزارش‌ها:\n"
            "• ارسال گزارش از داخل مدیریت کمپین.\n"
            "• «📤 ارسال به بالادست» از صفحهٔ همان کمپین.\n"
            "• خروجی شخصی: /myzip",
            reply_markup=adm_main_menu()
        )

    # ===== آمار =====
    if data == "adm:stats":
        return await q.edit_message_text(
            "آمار:\n"
            "• داخل هر کمپین، دکمهٔ «📊 آمار» را بزنید.\n"
            "• اگر آمار «واحد+پلتفرم» را فعال کرده‌اید، خروجی جزئی‌تر هم می‌بینید.",
            reply_markup=adm_main_menu()
        )

    import logging
    logging.warning("Unknown ADM action: %s", data)
    return await q.edit_message_text("⚠️ دکمه نامعتبر.", reply_markup=adm_main_menu())


async def superadmin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        if not await is_superadmin(s, update.effective_user.id):
            return await update.effective_message.reply_text("⛔️ فقط سوپرادمین.")
    await update.effective_message.reply_text("داشبورد مدیریت:", reply_markup=sa_main_menu())

async def sa_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    async with SessionLocal() as s:
        if not await is_superadmin(s, q.from_user.id):
            return await q.answer("⛔️ فقط سوپرادمین.", show_alert=True)

    data = q.data

    # منوی اصلی و بازگشت
    if data in ("sa", "sa:home"):
        return await q.edit_message_text("داشبورد مدیریت:", reply_markup=sa_main_menu())

    # شاخه "واحدها"
    if data == "sa:unit":
        return await q.edit_message_text("مدیریت واحدها:", reply_markup=sa_units_menu())

    if data == "sa:unit:list":
        # اگر می‌خواهی از همین‌جا لیست را باز کنی
        return await ul_start(update, context)

    if data == "sa:unit:admins":
        # فعلاً ریدایرکت به مرور واحدها؛ بعداً داخل لیست، نمایش/مدیریت ادمین‌ها را اضافه می‌کنیم
        return await ul_start(update, context)

    # شاخه "ادمین‌ها"
    if data == "sa:admin":
        return await q.edit_message_text("مدیریت ادمین‌ها:", reply_markup=sa_admins_menu())
    if data == "sa:admin:add":
        return await q.edit_message_text("`/addadmin <user_id> [role]` (پیش‌فرض L1)", parse_mode=ParseMode.MARKDOWN, reply_markup=sa_back_home())
    if data == "sa:admin:link":
        return await q.edit_message_text("`/linkadmin <child_id>` یا (SUPER) `/linkadmin <parent_id> <child_id>`",
                                         parse_mode=ParseMode.MARKDOWN, reply_markup=sa_back_home())
    if data == "sa:admin:tree":
        return await q.edit_message_text("نمایش درخت: `/myadmins`", parse_mode=ParseMode.MARKDOWN, reply_markup=sa_back_home())
    if data == "sa:admin:setrole":
        return await q.edit_message_text("`/setrole <admin_id> <SUPER|L1|L2>`", parse_mode=ParseMode.MARKDOWN, reply_markup=sa_back_home())

    # شاخه "کمپین‌ها"
    if data == "sa:camp":
        return await q.edit_message_text("مدیریت کمپین‌ها:", reply_markup=sa_campaigns_menu())
    if data == "sa:camp:list":
        # همان لیست/مدیریت کمپین‌ها که قبلاً داشتی
        await campaigns_cmd(update, context)
        return
    if data == "sa:camp:coverage":
        return await q.edit_message_text("از داخل صفحهٔ هر کمپین دکمهٔ «پوشش گزارش‌ها» را بزنید.", reply_markup=sa_back_home())

    # شاخه "گزارش‌ها"
    if data == "sa:report":
        return await q.edit_message_text("گزارش‌ها:", reply_markup=sa_reports_menu())
    if data == "sa:report:replyup":
        return await q.edit_message_text("برای ارسال گزارش به بالادست، داخل مدیریت کمپین «📤 ارسال گزارش به بالادست» را بزن.", reply_markup=sa_back_home())
    if data == "sa:report:export":
        return await q.edit_message_text("خروجی ZIP: از مدیریت کمپین «🗂️ خروجی ZIP» یا برای کاربر «/myzip».", reply_markup=sa_back_home())

    # آمار کل
    if data == "sa:stats":
        # مثال جمع‌آوری آمار
        from app import db_conn  # یا هر جایی که db_conn داری
        with db_conn() as c:
            camps = c.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"]
            users = c.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            units = c.execute("SELECT COUNT(*) c FROM units").fetchone()["c"]
            cities = c.execute("SELECT COUNT(*) c FROM cities").fetchone()["c"]
        text = f"📈 آمار کل:\n• کمپین‌ها: {camps}\n• کاربران: {users}\n• واحدها: {units}\n• شهرها: {cities}"
        return await q.edit_message_text(text, reply_markup=sa_back_home())

    # هر چیز دیگر = ناشناخته
    import logging
    logging.warning("Unknown SA action: %s", data)
    return await q.edit_message_text(f"⚠️ دکمه ناشناخته: `{data}`", parse_mode="Markdown", reply_markup=sa_main_menu())



# ارسال گزارش به ویزارد گزارش در فایل report.py
async def report_submit_to_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # q = update.callback_query
    # await safe_answer(q)  # پاسخ به callback query

    uid = update.effective_user.id
    async with SessionLocal() as s:
        if await is_superadmin(s, uid) or await is_admin(s, uid):
            # هدایت به ویزارد گزارش برای سوپرادمین و ادمین
            return await report_cmd(update, context)  # فراخوانی مستقیم تابع report_cmd برای شروع ویزارد

    return REPORT_PICK_CAMPAIGN  # اگر کاربر سوپرادمین یا ادمین نباشد، فرایند را متوقف می‌کند
