# keyboards.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

# ------------------ Units ------------------
UNIT_TYPE_LABELS = {
    "COUNTRY": "🌍 کشور",
    "OSTAN":   "🏛 استان",
    "SHAHR":   "🏙 شهر",
    "HOZE":    "🕌 حوزه",
    "PAYGAH":  "🏢 پایگاه",
}
UNIT_ALLOWED_PARENTS = {
    "COUNTRY": [],
    "OSTAN":   ["COUNTRY"],
    "SHAHR":   ["OSTAN"],
    "HOZE":    ["SHAHR"],
    "PAYGAH":  ["HOZE"],
}
VALID_TYPES = {"COUNTRY","OSTAN","SHAHR","HOZE","PAYGAH"}

# ------------------ Labels / Buttons ------------------
# سوپرادمین (reply keyboard)
BTN_SA_DASH = "🧭 داشبورد مدیریت"

# کاربر
BTN_REPORT   = "📤 ارسال گزارش"
BTN_MYSTATS  = "📊 آمار من"
BTN_MYEXPORT = "🗂️ خروجی من"

# ادمین
BTN_PANEL  = "🧊 پنل ادمین"
BTN_CAMPS  = "📋 کمپین‌ها"
BTN_NEW    = "🆕 ساخت کمپین"

BTN_ADDUSR = "➕ افزودن کاربر"
BTN_MYUSRS = "👥 کاربران من"
BTN_DELUSR = "➖ حذف کاربر"
BTN_RENAME = "✏️ تغییر نام کاربر"

# مدیریت ادمین‌ها (راهنمایی در Reply)
BTN_ADDADMIN  = "👑 افزودن ادمین"
BTN_LINKADMIN = "🔗 اتصال والد/فرزند"
BTN_MYADMINS  = "🧭 زیرمجموعه‌ها"

# مدیریت واحدها (reply keyboard shortcuts)
BTN_UNIT_MENU   = "🏗️ مدیریت واحدها"
BTN_UNIT_ADD    = "➕ ساخت واحد"
BTN_UNIT_LIST   = "📜 لیست واحدها"
BTN_UNIT_ATTACH = "🔗 اتصال ادمین به واحد"

# ------------------ Platforms ------------------
PLATFORMS = [
    ("group", "◽️ گروه"),
    ("supergroup", "◽️ سوپرگروه"),
    ("channel", "◽️ کانال"),
    ("broadcast", "◽️ برودکست"),
    ("neighborhood_media", "◽️ رسانه‌محله"),
    ("instagram", "◽️ اینستاگرام"),
    ("twitter", "◽️ توییتر"),
    ("telegram", "◽️ تلگرام"),
    ("non_telegram", "◽️ غیرتلگرامی"),
]
PLATFORM_KEYS = [k for k, _ in PLATFORMS]
PLATFORM_LABEL = {k: v for k, v in PLATFORMS}

# ------------------ Reply Keyboards ------------------
def user_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_REPORT],
            [BTN_MYSTATS, BTN_MYEXPORT],
        ],
        resize_keyboard=True
    )

def admin_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            # [BTN_REPORT],
            [BTN_SA_DASH],
        ],
        resize_keyboard=True,
    )

def superadmin_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            # [BTN_REPORT],
            [BTN_SA_DASH],
        ],
        resize_keyboard=True
    )

# ------------------ Inline Keyboards ------------------
# keyboards.py
# keyboards.py
def platforms_keyboard(selected: list[str] | None = None, prefix: str = "pf", done_label: str = "💾 ثبت") -> InlineKeyboardMarkup:
    selected = selected or []
    rows = []
    for k, v in PLATFORMS:
        mark = "✅ " if k in selected else "◻️ "
        rows.append([InlineKeyboardButton(f"{mark}{v}", callback_data=f"{prefix}:{k}")])
    rows.append([InlineKeyboardButton(done_label, callback_data=f"{prefix}:done")])
    return InlineKeyboardMarkup(rows)

# ===== Superadmin inline menus (برای flows/superadmin.py) =====
def adm_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 کمپین‌ها", callback_data="adm:camp")],
        [InlineKeyboardButton("🏗️ واحدها", callback_data="adm:unit")],
        [InlineKeyboardButton("👤 پروفایل من", callback_data="adm:profile")],     # ← جدید
        [InlineKeyboardButton("📊 آمار واحد من", callback_data="adm:unit:stats")],
        [InlineKeyboardButton("🗂️ خروجی واحد من", callback_data="adm:unit:export")],
        [InlineKeyboardButton("📤 ارسال گزارش", callback_data="adm:report:submit")],
    ])


def adm_campaigns_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 ساخت کمپین", callback_data="nc:start")],   # ← از همان ویزارد ساخت استفاده می‌کند
        [InlineKeyboardButton("📋 کمپین‌های من", callback_data="adm:camp:list")],
        [InlineKeyboardButton("◀️ بازگشت", callback_data="adm:home")],
    ])

def adm_units_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ساخت واحد", callback_data="adm:unit:add")],
        [InlineKeyboardButton("📜 لیست واحدها", callback_data="adm:unit:list")],
        [InlineKeyboardButton("🔗 اتصال ادمین به واحد", callback_data="adm:unit:attach")],
        [InlineKeyboardButton("◀️ بازگشت", callback_data="adm:home")],
    ])



def sa_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 کمپین‌ها", callback_data="sa:camp")],
        [InlineKeyboardButton("🏗️ واحدها", callback_data="sa:unit")],
        [InlineKeyboardButton("👑 ادمین‌ها", callback_data="sa:admin")],
        [InlineKeyboardButton("👤 پروفایل من", callback_data="sa:profile")],      # ← جدید
        [InlineKeyboardButton("📊 آمار واحدها", callback_data="sa:unit:stats")],
        [InlineKeyboardButton("🗂️ خروجی واحدها", callback_data="sa:unit:export")],
        [InlineKeyboardButton("📤 ارسال گزارش", callback_data="sa:report:submit")],
    ])


def sa_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ بازگشت", callback_data="sa:home")]])

def sa_admins_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن ادمین", callback_data="sa:admin:add")],
        [InlineKeyboardButton("🔗 والد/فرزند", callback_data="sa:admin:link")],
        [InlineKeyboardButton("🧭 درخت زیرمجموعه‌ها", callback_data="sa:admin:tree")],
        [InlineKeyboardButton("🛂 تغییر نقش", callback_data="sa:admin:setrole")],
        [InlineKeyboardButton("◀️ بازگشت", callback_data="sa:home")],
    ])

def sa_units_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ساخت واحد", callback_data="sa:unit:add")],
        [InlineKeyboardButton("📜 لیست واحدها", callback_data="sa:unit:list")],
        [InlineKeyboardButton("🔗 اتصال ادمین به واحد", callback_data="sa:unit:attach")],
        # [InlineKeyboardButton("👤 ادمین‌های واحدها", callback_data="sa:unit:admins")],  # ← این
        [InlineKeyboardButton("◀️ بازگشت", callback_data="sa:home")],
    ])

def sa_campaigns_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 ساخت کمپین", callback_data="nc:start")],
        [InlineKeyboardButton("📋 کمپین‌های من", callback_data="sa:camp:list")],
        # [InlineKeyboardButton("✅ پوشش گزارش‌ها", callback_data="sa:camp:coverage")],
        [InlineKeyboardButton("◀️ بازگشت", callback_data="sa:home")],
    ])

def sa_reports_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗂️ خروجی‌ها (ZIP)", callback_data="sa:report:export")],
        [InlineKeyboardButton("◀️ بازگشت", callback_data="sa:home")],
    ])

def campaigns_inline_keyboard(camps, manage: bool=False) -> InlineKeyboardMarkup:
    rows = []
    for c in camps:
        label = f"▫️ #{c.id} | {c.name} {'🟢' if c.active else '🔴'}"
        cb   = f"camp:{c.id}"  # Callback data for each campaign
        rows.append([InlineKeyboardButton(label, callback_data=cb)])
    if not rows:
        rows = [[InlineKeyboardButton("(خالی)", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)