# flows/common.py (مثلاً)
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from keyboards import admin_reply_kb  # یا هر منوی پیش‌فرض

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # پاکسازی فلگ/داده‌های موقّتِ ما
    for k in ("edit_field", "edit_platforms", "_in_conversation", "state", "tmp", "await_edit_text"):
        context.user_data.pop(k, None)

    # پیام خروج
    await update.effective_message.reply_text(
        "❌ فرآیند لغو شد. می‌توانید از منوی اصلی ادامه دهید.",
        reply_markup=admin_reply_kb()
    )

    # اگر داخل یک ConversationHandler باشیم، این مقدار پایان گفتگوست
    return ConversationHandler.END
