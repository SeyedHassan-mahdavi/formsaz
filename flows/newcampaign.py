# -*- coding: utf-8 -*-
from __future__ import annotations
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from keyboards import platforms_keyboard, PLATFORM_LABEL, PLATFORM_KEYS, admin_reply_kb, superadmin_reply_kb
from crud import create_campaign_v2, get_primary_unit_for_admin, is_admin
from utils import now_iso
from flows.common import cancel_cmd


NEWCAMP_NAME, NEWCAMP_HASHTAG, NEWCAMP_CITY, NEWCAMP_PLATFORMS, NEWCAMP_DESC, NEWCAMP_CONFIRM = range(6)

async def newcampaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from database import SessionLocal
    u = update.effective_user
    async with SessionLocal() as s:
        if not await is_admin(s, u.id):
            await update.effective_message.reply_text("⛔️ فقط ادمین‌ها می‌توانند کمپین بسازند.")
            return ConversationHandler.END
    context.user_data["_in_conversation"] = True
    await update.effective_message.reply_text(
        "نام کمپین را بفرستید (اجباری):\n\nبرای خروج /cancel را بزنید.",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["conv"] = "newcamp"
    return NEWCAMP_NAME

async def newcamp_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = NEWCAMP_HASHTAG
    context.user_data["tmp"] = {"name": update.message.text.strip()}
    await update.message.reply_text("هشتگ؟ (اختیاری، یا /skip)")
    return NEWCAMP_HASHTAG

async def newcamp_hashtag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tmp"]["hashtag"] = update.message.text.strip()
    context.user_data["state"] = NEWCAMP_CITY
    await update.message.reply_text("نام شهر؟ (اختیاری، یا /skip)")
    return NEWCAMP_CITY

async def newcamp_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tmp"]["city"] = update.message.text.strip()
    context.user_data["tmp"]["platforms"] = []
    context.user_data["state"] = NEWCAMP_PLATFORMS
    await update.message.reply_text(
        "پلتفرم‌ها را انتخاب کنید (چندتایی). دکمه‌ها را بزنید و سپس «ادامه» را بزنید:",
        reply_markup=platforms_keyboard([])
    )
    return NEWCAMP_PLATFORMS

async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from database import SessionLocal
    state = context.user_data.get("state")
    tmp = context.user_data.get("tmp", {})
    if context.user_data.get("conv") == "newcamp":
        if state == NEWCAMP_HASHTAG:
            tmp["hashtag"] = None
            context.user_data["state"] = NEWCAMP_CITY
            await update.message.reply_text("نام شهر؟ (اختیاری، یا /skip)")
            return NEWCAMP_CITY
        if state == NEWCAMP_CITY:
            tmp["city"] = None
            tmp["platforms"] = []
            context.user_data["state"] = NEWCAMP_PLATFORMS
            await update.message.reply_text("پلتفرم‌ها را انتخاب کنید (چندتایی). سپس «ادامه» را بزنید:",
                                            reply_markup=platforms_keyboard([]))
            return NEWCAMP_PLATFORMS
        if state == NEWCAMP_DESC:
            tmp["description"] = None
            d = tmp
            plats = ", ".join([PLATFORM_LABEL[x] for x in d["platforms"]]) if d.get("platforms") else "-"
            await update.message.reply_text(
                f"تایید ایجاد کمپین؟\n"
                f"نام: {d.get('name')}\n"
                f"هشتگ: {d.get('hashtag') or '-'}\n"
                f"شهر: {d.get('city') or '-'}\n"
                f"پلتفرم‌ها: {plats}\n"
                f"توضیحات: {d.get('description') or '-'}\n\n/confirm یا /cancel"
            )
            context.user_data["state"] = NEWCAMP_CONFIRM
            return NEWCAMP_CONFIRM
    await update.message.reply_text("رد شد.")

async def platform_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    from utils import safe_answer
    await safe_answer(q)
    data = q.data.split(":", 1)[1]
    tmp = context.user_data.get("tmp", {})
    picked = tmp.get("platforms", [])
    if data == "done":
        if not picked:
            await q.edit_message_text("حداقل یک پلتفرم انتخاب کنید.", reply_markup=platforms_keyboard(picked))
            return NEWCAMP_PLATFORMS
        context.user_data["state"] = NEWCAMP_DESC
        await q.edit_message_text("توضیحات؟ (اختیاری، یا /skip)")
        return NEWCAMP_DESC
    if data in PLATFORM_KEYS:
        if data in picked: picked.remove(data)
        else: picked.append(data)
        tmp["platforms"] = picked
        context.user_data["tmp"] = tmp
        await q.edit_message_reply_markup(reply_markup=platforms_keyboard(picked))
        return NEWCAMP_PLATFORMS

async def newcamp_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tmp"]["description"] = update.message.text.strip()
    d = context.user_data["tmp"]
    plats = ", ".join([PLATFORM_LABEL[x] for x in d["platforms"]]) if d.get("platforms") else "-"
    await update.message.reply_text(
        f"تایید ایجاد کمپین؟\n"
        f"نام: {d['name']}\n"
        f"هشتگ: {d.get('hashtag') or '-'}\n"
        f"شهر: {d.get('city') or '-'}\n"
        f"پلتفرم‌ها: {plats}\n"
        f"توضیحات: {d.get('description') or '-'}\n\n/confirm یا /cancel"
    )
    return NEWCAMP_CONFIRM

async def newcamp_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from database import SessionLocal
    admin_id = update.effective_user.id
    async with SessionLocal() as s:
        d = context.user_data.pop("tmp", {})
        if not d or not d.get("name") or not d.get("platforms"):
            await update.message.reply_text("اطلاعات ناقص است."); return ConversationHandler.END
        owner_unit_id = await get_primary_unit_for_admin(s, admin_id)
        if not owner_unit_id:
            await update.message.reply_text("برای شما واحدی تعریف نشده. ابتدا واحد/نقش شما را بسازید.")
            return ConversationHandler.END
        cid = await create_campaign_v2(
            session=s,
            owner_unit_id=owner_unit_id,
            owner_admin_id=admin_id,
            name=d["name"],
            platforms=d["platforms"],
            description=d.get("description"),
            hashtag=d.get("hashtag"),
            city_label=d.get("city"),
            config={}
        )
        await s.commit()
    await update.message.reply_text(f"کمپین #{cid} ساخته شد ✅")
    context.user_data.pop("_in_conversation", None)
    # بازگرداندن منو (همان قبل)
    from keyboards import superadmin_reply_kb, admin_reply_kb
    # به دلیل عدم دسترسی sync به نقش، منوی ادمین را می‌دهیم؛ منوی SUPER در start چک می‌شود
    await update.effective_message.reply_text("منوی ادمین:", reply_markup=admin_reply_kb())
    return ConversationHandler.END

def build_conversation():
    return ConversationHandler(
        entry_points=[
            CommandHandler("newcampaign", newcampaign),
            CallbackQueryHandler(newcampaign, pattern=r"^nc:start$"),
        ],
        states={
            NEWCAMP_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, newcamp_name)],
            NEWCAMP_HASHTAG:   [CommandHandler("skip", skip), MessageHandler(filters.TEXT & ~filters.COMMAND, newcamp_hashtag)],
            NEWCAMP_CITY:      [CommandHandler("skip", skip), MessageHandler(filters.TEXT & ~filters.COMMAND, newcamp_city)],
            NEWCAMP_PLATFORMS: [CallbackQueryHandler(platform_toggle, pattern=r"^pf:")],
            NEWCAMP_DESC:      [CommandHandler("skip", skip), MessageHandler(filters.TEXT & ~filters.COMMAND, newcamp_desc)],
            NEWCAMP_CONFIRM:   [CommandHandler("confirm", newcamp_confirm)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_cmd),
        ],
        allow_reentry=True,
    )
