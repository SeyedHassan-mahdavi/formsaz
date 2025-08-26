# -*- coding: utf-8 -*-
from __future__ import annotations

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from sqlalchemy import select, func

from database import SessionLocal
from crud import is_admin, is_superadmin, get_primary_unit_for_admin
from utils import now_iso
from models import Unit, UnitAdmin, Admin
from keyboards import sa_units_menu, adm_units_menu


# مراحل ویزارد
UNIT_WIZ_TYPE, UNIT_WIZ_NAME, UNIT_WIZ_PARENT, UNIT_WIZ_PARENT_SEARCH, UNIT_WIZ_CONFIRM = range(5)

PARENT_ALLOWED = {
    "COUNTRY": None,
    "OSTAN":   "COUNTRY",
    "SHAHR":   "OSTAN",
    "HOZE":    "SHAHR",
    "PAYGAH":  "HOZE",
}

PAGE_SIZE = 8  # تعداد آیتم‌ها در هر صفحه برای لیست والدها

# اگر VALID_TYPES در keyboards تعریف شده از همان استفاده کن
try:
    from keyboards import UNIT_TYPE_LABELS, VALID_TYPES
except Exception:
    UNIT_TYPE_LABELS = {
        "COUNTRY": "🌍 کشور",
        "OSTAN":   "🏛 استان",
        "SHAHR":   "🏙 شهر",
        "HOZE":    "🕌 حوزه",
        "PAYGAH":  "🏢 پایگاه",
    }
    VALID_TYPES = {"COUNTRY", "OSTAN", "SHAHR", "HOZE", "PAYGAH"}

# برچسب نوع بدون ایموجی (برای لیست فقط‌خواندنی)
TEXT_TYPE_LABELS = {
    "COUNTRY": "کشور",
    "OSTAN":   "استان",
    "SHAHR":   "شهر",
    "HOZE":    "حوزه",
    "PAYGAH":  "پایگاه",
}


# ---- Scope helpers ----
async def _get_scope_info(session, user_id: int):
    """برمی‌گرداند: (is_super, scope_root_id, scope_root_type)"""
    is_super = await is_superadmin(session, user_id)
    if is_super:
        return True, None, None
    root_id = await get_primary_unit_for_admin(session, user_id)
    if not root_id:
        return False, None, None
    ru = await session.get(Unit, root_id)
    return False, root_id, (ru.type if ru else None)

def _is_descendant_type(child_t: str, ancestor_t: str) -> bool:
    """آیا child_t در سطح پایین‌تر از ancestor_t است؟ (هم‌سطح یا بالاتر = False)"""
    if child_t == ancestor_t:
        return False
    cur = child_t
    while True:
        parent = PARENT_ALLOWED.get(cur)
        if parent is None:
            return False
        if parent == ancestor_t:
            return True
        cur = parent

def _allowed_types_for_user(is_super: bool, root_type: str | None) -> set[str]:
    """نوع‌هایی که کاربر اجازه ساخت دارد."""
    if is_super or not root_type:
        return set(VALID_TYPES)
    return {t for t in VALID_TYPES if _is_descendant_type(t, root_type)}

async def _unit_has_ancestor(session, unit: Unit, ancestor_id: int) -> bool:
    """بررسی می‌کند واحدِ داده‌شده در زیرمجموعه‌ی ancestor_id هست یا نه."""
    if ancestor_id is None:
        return True
    cur = unit
    while cur:
        if cur.id == ancestor_id:
            return True
        if not cur.parent_id:
            return False
        cur = await session.get(Unit, cur.parent_id)
    return False


# -------------------- دستورات CLI --------------------
async def unit_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        uid = update.effective_user.id
        if not await is_admin(s, uid):
            return await update.message.reply_text("⛔️ شما ادمین نیستید.")

        parts = (update.message.text or "").split(maxsplit=3)
        if len(parts) < 3 or parts[1].upper() not in VALID_TYPES:
            return await update.message.reply_text(
                "فرمت: /unit_add <COUNTRY|OSTAN|SHAHR|HOZE|PAYGAH> <name> [parent_id]"
            )

        utype = parts[1].upper()
        name = parts[2].strip()
        parent_id = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else None

        # محدودیت‌های دسترسی
        is_super, scope_root_id, root_type = await _get_scope_info(s, uid)
        allowed_types = _allowed_types_for_user(is_super, root_type)
        if utype not in allowed_types:
            return await update.message.reply_text("⛔️ شما مجاز به ساخت این نوع واحد نیستید.")

        expected_parent_type = PARENT_ALLOWED.get(utype)
        if expected_parent_type is None:
            # فقط سوپرادمین اجازه ساخت COUNTRY (بدون والد) دارد
            if not is_super:
                return await update.message.reply_text("⛔️ شما مجاز به ساخت این نوع واحد نیستید.")
        else:
            if not parent_id:
                return await update.message.reply_text("برای این نوع واحد، parent_id الزامی است.")
            p = await s.get(Unit, parent_id)
            if not p or p.type != expected_parent_type:
                return await update.message.reply_text("parent_id معتبر نیست.")
            if not is_super and scope_root_id:
                if not await _unit_has_ancestor(s, p, scope_root_id):
                    return await update.message.reply_text("⛔️ والد انتخابی خارج از محدودهٔ دسترسی شماست.")

        u = Unit(name=name, type=utype, parent_id=parent_id, created_at=now_iso())
        s.add(u)
        await s.flush()

        s.add(UnitAdmin(unit_id=u.id, admin_id=uid, role="OWNER"))
        await s.commit()

        await update.message.reply_text(
            f"✅ واحد #{u.id} ({utype}) با نام «{name}» ایجاد شد" +
            (f" و به والد #{parent_id} وصل شد." if parent_id else ".")
        )


async def unit_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parent_id = None
    if context.args and len(context.args) >= 1 and str(context.args[0]).isdigit():
        parent_id = int(context.args[0])

    async with SessionLocal() as s:
        if parent_id is not None:
            rows = (await s.execute(
                select(Unit).where(Unit.parent_id == parent_id).order_by(Unit.type, Unit.name)
            )).scalars().all()
            if not rows:
                return await update.message.reply_text(f"زیرمجموعه‌ای برای #{parent_id} نیست.")
            lines = [f"زیرمجموعه‌های واحد #{parent_id}:"]
        else:
            rows = (await s.execute(
                select(Unit).where(Unit.parent_id.is_(None)).order_by(Unit.type, Unit.name)
            )).scalars().all()
            if not rows:
                return await update.message.reply_text("واحد ریشه‌ای تعریف نشده.")
            lines = ["واحدهای ریشه:"]

        for r in rows:
            lines.append(f"- #{r.id} | {r.type} | {r.name}")

    await update.message.reply_text("\n".join(lines))


async def unit_attach_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        if not await is_admin(s, update.effective_user.id):
            return await update.message.reply_text("⛔️ شما ادمین نیستید.")

        parts = (update.message.text or "").split()
        if len(parts) < 4 or not parts[1].isdigit() or not parts[2].isdigit():
            return await update.message.reply_text(
                "فرمت: /unit_attach <unit_id> <admin_id> <OWNER|ASSISTANT>"
            )

        unit_id = int(parts[1])
        admin_id = int(parts[2])
        role = parts[3].upper()

        if role not in {"OWNER", "ASSISTANT"}:
            return await update.message.reply_text("role باید OWNER یا ASSISTANT باشد.")

        unit = await s.get(Unit, unit_id)
        if not unit:
            return await update.message.reply_text("واحد معتبر نیست.")

        if not await s.get(Admin, admin_id):
            s.add(Admin(admin_id=admin_id, role="L1"))
            await s.flush()

        existing = await s.get(UnitAdmin, {"unit_id": unit_id, "admin_id": admin_id})
        if existing:
            existing.role = role
        else:
            s.add(UnitAdmin(unit_id=unit_id, admin_id=admin_id, role=role))

        await s.commit()
        await update.message.reply_text(f"✅ admin {admin_id} به واحد #{unit_id} با نقش {role} وصل شد.")


# -------------------- ویزارد --------------------
async def unit_wizard_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        uid = update.effective_user.id
        if not await is_admin(s, uid):
            if update.message:
                return await update.message.reply_text("⛔️ شما ادمین نیستید.")
            elif update.callback_query:
                return await update.callback_query.answer("⛔️ شما ادمین نیستید.", show_alert=True)

        is_super, scope_root_id, root_type = await _get_scope_info(s, uid)
        if not is_super and not scope_root_id:
            msg = "برای شما واحد اصلی ثبت نشده. از سوپرادمین بخواهید شما را به یک واحد وصل کند."
            if update.message:
                return await update.message.reply_text(msg)
            else:
                await update.callback_query.edit_message_text(msg)
                return ConversationHandler.END

    context.user_data["uw_is_super"] = is_super
    context.user_data["uw_scope_root"] = scope_root_id
    context.user_data["uw_allowed_types"] = _allowed_types_for_user(is_super, root_type)

    # دکمه‌ها فقط برای نوع‌های مجاز
    allowed_types = context.user_data["uw_allowed_types"]
    buttons = [
        [InlineKeyboardButton(UNIT_TYPE_LABELS[t], callback_data=f"uw:type:{t}")]
        for t in [t for t in UNIT_TYPE_LABELS.keys() if t in allowed_types]
    ]
    if not buttons:
        if update.message:
            return await update.message.reply_text("⛔️ شما اجازه ساخت هیچ نوع واحدی در محدودهٔ خود ندارید.")
        else:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("⛔️ شما اجازه ساخت هیچ نوع واحدی در محدودهٔ خود ندارید.")
            return ConversationHandler.END

    if update.message:
        await update.message.reply_text("نوع واحد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text("نوع واحد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons))
    return UNIT_WIZ_TYPE


async def unit_wiz_pick_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    utype = q.data.split(":")[2]

    allowed = context.user_data.get("uw_allowed_types", set(VALID_TYPES))
    if utype not in allowed:
        return await q.answer("⛔️ مجاز نیستید این نوع را بسازید.", show_alert=True)

    context.user_data["unit_wiz_type"] = utype
    label = UNIT_TYPE_LABELS.get(utype, utype)
    await q.edit_message_text(f"✅ نوع انتخاب شد: {label}\n\nنام واحد را وارد کنید:")
    return UNIT_WIZ_NAME


async def unit_wiz_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data["unit_wiz_name"] = name

    utype = context.user_data["unit_wiz_type"]
    parent_type = PARENT_ALLOWED.get(utype)

    # اگر والد لازم نیست (COUNTRY)
    if parent_type is None:
        context.user_data["unit_wiz_parent"] = None
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("تایید", callback_data="uw:confirm")],
            [InlineKeyboardButton("لغو", callback_data="uw:cancel")],
        ])
        text = (
            f"نوع: {utype}\n"
            f"نام: {name}\n"
            f"والد: --- (نیازی به والد ندارد)"
        )
        await update.message.reply_text(text + "\n\nآیا تایید می‌کنید؟", reply_markup=kb)
        return UNIT_WIZ_CONFIRM

    # والد لازم است → صفحهٔ لیست/جستجو/مرتب‌سازی
    context.user_data["pp_page"] = 0
    context.user_data["pp_query"] = None
    context.user_data["pp_sort"] = "name_asc"
    context.user_data["pp_parent_type"] = parent_type

    await _pp_render_parent_list(
        update.message,
        parent_type=parent_type,
        page=0,
        q=None,
        sort_key="name_asc",
        edit=False,
        scope_root_id=context.user_data.get("uw_scope_root"),
        is_super=context.user_data.get("uw_is_super", False),
    )
    return UNIT_WIZ_PARENT


async def unit_wiz_parent_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # حالت‌ها از user_data
    page = int(context.user_data.get("pp_page", 0))
    qtext = context.user_data.get("pp_query")
    sort_key = context.user_data.get("pp_sort", "name_asc")
    parent_type = context.user_data.get("pp_parent_type")

    # بازگشت به ورود نام
    if data == "uw:pp:back":
        await q.edit_message_text("نام واحد را وارد کنید:")
        return UNIT_WIZ_NAME

    # شروع جستجو
    if data == "uw:pp:search":
        await q.edit_message_text("عبارت جستجو را ارسال کنید (چند حرف از نام والد):")
        return UNIT_WIZ_PARENT_SEARCH

    # انتخاب والد
    if data.startswith("uw:pp:pick:"):
        parent_id = int(data.split(":")[3])
        async with SessionLocal() as s:
            p = await s.get(Unit, parent_id)

        if not p or p.type != parent_type:
            await q.edit_message_text("والد معتبر/هم‌نوع پیدا نشد. دوباره انتخاب کنید.")
            await _pp_render_parent_list(
                q.message,
                parent_type=parent_type, page=page, q=qtext, sort_key=sort_key, edit=True,
                scope_root_id=context.user_data.get("uw_scope_root"),
                is_super=context.user_data.get("uw_is_super", False),
            )
            return UNIT_WIZ_PARENT

        # ✅ این‌جاست: محدودیت محدوده
        scope_root_id = context.user_data.get("uw_scope_root")
        is_super = context.user_data.get("uw_is_super", False)
        async with SessionLocal() as s2:
            if not is_super and scope_root_id and not await _unit_has_ancestor(s2, p, scope_root_id):
                await q.edit_message_text("⛔️ این والد خارج از محدودهٔ دسترسی شماست.")
                await _pp_render_parent_list(
                    q.message,
                    parent_type=parent_type, page=page, q=qtext, sort_key=sort_key, edit=True,
                    scope_root_id=scope_root_id, is_super=is_super
                )
                return UNIT_WIZ_PARENT

        # تایید والد و رفتن به صفحه‌ی تایید نهایی
        context.user_data["unit_wiz_parent"] = parent_id

        utype = context.user_data["unit_wiz_type"]
        name = context.user_data["unit_wiz_name"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("تایید", callback_data="uw:confirm")],
            [InlineKeyboardButton("لغو", callback_data="uw:cancel")],
        ])
        text = (
            f"نوع: {utype}\n"
            f"نام: {name}\n"
            f"والد: {p.name} (#{p.id})"
        )
        await q.edit_message_text(text + "\n\nآیا تایید می‌کنید؟", reply_markup=kb)
        return UNIT_WIZ_CONFIRM

    if data.startswith("uw:pp:list:"):
        _, _, _, page_s, sort_key, *rest = data.split(":")
        page = int(page_s)
        qtext = ":".join(rest) if rest else ""
        qtext = qtext or None

        context.user_data["pp_page"] = page
        context.user_data["pp_query"] = qtext
        context.user_data["pp_sort"] = sort_key

        await _pp_render_parent_list(
            q.message,
            parent_type=parent_type, page=page, q=qtext, sort_key=sort_key, edit=True,
            scope_root_id=context.user_data.get("uw_scope_root"),
            is_super=context.user_data.get("uw_is_super", False),
        )
        return UNIT_WIZ_PARENT

    if data.startswith("uw:pp:sort:"):
        _, _, _, cur = data.split(":")
        sort_key = _pp_next_sort(cur)
        context.user_data["pp_sort"] = sort_key
        context.user_data["pp_page"] = 0
        await _pp_render_parent_list(
            q.message,
            parent_type=parent_type, page=0, q=qtext, sort_key=sort_key, edit=True,
            scope_root_id=context.user_data.get("uw_scope_root"),
            is_super=context.user_data.get("uw_is_super", False),
        )
        return UNIT_WIZ_PARENT

        # پیش‌فرض: در همین state بمان
        return UNIT_WIZ_PARENT


async def unit_wiz_parent_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_q = (update.message.text or "").strip() or None
    context.user_data["pp_query"] = search_q
    context.user_data["pp_page"] = 0
    sort_key = context.user_data.get("pp_sort", "name_asc")
    parent_type = context.user_data.get("pp_parent_type")

    await _pp_render_parent_list(
        update.message,
        parent_type=parent_type,
        page=0,
        q=search_q,
        sort_key=sort_key,
        edit=False,
        scope_root_id=context.user_data.get("uw_scope_root"),
        is_super=context.user_data.get("uw_is_super", False),
    )
    return UNIT_WIZ_PARENT


async def _fetch_parents_page(session, parent_type: str, page: int, q: str | None):
    query = select(Unit).where(Unit.type == parent_type)
    if q:
        like = f"%{q}%"
        query = query.where(func.lower(Unit.name).like(func.lower(like)))
    query = query.order_by(Unit.name).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
    rows = (await session.execute(query)).scalars().all()

    # شمارش کل برای ساخت دکمه‌های صفحه‌بندی
    count_q = select(func.count()).select_from(Unit).where(Unit.type == parent_type)
    if q:
        like = f"%{q}%"
        count_q = count_q.where(func.lower(Unit.name).like(func.lower(like)))
    total = (await session.execute(count_q)).scalar_one()

    return rows, total

def _parent_nav_kb(page: int, total: int, q: str | None):
    buttons = []
    nav = []
    max_page = max(0, (total - 1) // PAGE_SIZE)
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"uw:pp:page:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"uw:pp:page:{page+1}"))
    if nav:
        buttons.append(nav)

    tools = [
        InlineKeyboardButton("🔍 جستجو", callback_data="uw:pp:search"),
    ]
    if q:
        tools.append(InlineKeyboardButton("🧹 پاک کردن جستجو", callback_data="uw:pp:clear"))
    tools.append(InlineKeyboardButton("↩️ برگشت", callback_data="uw:pp:back"))
    buttons.append(tools)

    return buttons


async def unit_wiz_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "uw:cancel":
        await q.edit_message_text("❌ عملیات لغو شد.")
        return ConversationHandler.END

    utype = context.user_data["unit_wiz_type"]
    name = context.user_data["unit_wiz_name"]
    parent_id = context.user_data.get("unit_wiz_parent")

    # اعتبارسنجی نهایی سلسله‌مراتب + محدوده
    expected_parent_type = PARENT_ALLOWED.get(utype)

    async with SessionLocal() as s:
        is_super, scope_root_id, root_type = await _get_scope_info(s, q.from_user.id)
        allowed_types = _allowed_types_for_user(is_super, root_type)
        if utype not in allowed_types:
            await q.edit_message_text("⛔️ شما مجاز به ساخت این نوع واحد نیستید.")
            return ConversationHandler.END

        if expected_parent_type is None:
            if not is_super:
                await q.edit_message_text("⛔️ شما مجاز به ساخت این نوع واحد نیستید.")
                return ConversationHandler.END
        else:
            if not parent_id:
                await q.edit_message_text("❗️ برای این نوع واحد انتخاب والد الزامی است.")
                return ConversationHandler.END
            p = await s.get(Unit, parent_id)
            if not p or p.type != expected_parent_type:
                await q.edit_message_text("❗️ نوع والد انتخابی معتبر نیست. دوباره تلاش کنید.")
                return ConversationHandler.END
            if not is_super and scope_root_id and not await _unit_has_ancestor(s, p, scope_root_id):
                await q.edit_message_text("⛔️ والد انتخابی خارج از محدودهٔ شماست.")
                return ConversationHandler.END

        u = Unit(name=name, type=utype, parent_id=parent_id, created_at=now_iso())
        s.add(u)
        await s.flush()
        s.add(UnitAdmin(unit_id=u.id, admin_id=q.from_user.id, role="OWNER"))
        await s.commit()

    await q.edit_message_text(f"✅ واحد #{u.id} ({UNIT_TYPE_LABELS.get(utype, utype)}) با نام «{name}» ساخته شد.")
    return ConversationHandler.END


# -------------------- ConversationHandler --------------------
# ✅ ویزارد ساخت واحد (بدون تداخل با CLI /unit_add)
def build_unit_wizard_conversation():
    async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await update.message.reply_text("❌ عملیات لغو شد.")
        elif update.callback_query:
            await update.callback_query.answer("❌ عملیات لغو شد.", show_alert=True)

    return ConversationHandler(
        entry_points=[
            # دستور جدا برای ویزارد تا با /unit_add (CLI) قاطی نشه
            CommandHandler("unit_add_wiz", unit_wizard_start),
            # هم سوپرادمین هم ادمین
            CallbackQueryHandler(unit_wizard_start, pattern=r"^(sa|adm):unit:add$"),
        ],
        states={
            UNIT_WIZ_TYPE: [
                CallbackQueryHandler(unit_wiz_pick_type, pattern=r"^uw:type:")
            ],
            UNIT_WIZ_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, unit_wiz_receive_name)
            ],
            UNIT_WIZ_PARENT: [
                CallbackQueryHandler(
                    unit_wiz_parent_router,
                    pattern=r"^uw:pp:(pick:\d+|list:\d+:(name_asc|name_desc|new):.*|sort:(name_asc|name_desc|new)|search|back)$"
                )
            ],
            UNIT_WIZ_PARENT_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, unit_wiz_parent_search_input)
            ],
            UNIT_WIZ_CONFIRM: [
                CallbackQueryHandler(unit_wiz_confirm, pattern=r"^uw:(confirm|cancel)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        allow_reentry=True,
    )

# ===== Parent-picker (shared) – no emojis, with sort/search/paging =====

PP_PAGE_SIZE = 8  # اندازه صفحه در انتخاب والد
PP_SORTS = ("name_asc", "name_desc", "new")  # چرخه مرتب‌سازی

def _pp_next_sort(cur: str) -> str:
    try:
        i = PP_SORTS.index(cur)
        return PP_SORTS[(i + 1) % len(PP_SORTS)]
    except ValueError:
        return "name_asc"

def _pp_order_clause(sort_key: str):
    # از sqlalchemy import func
    if sort_key == "name_desc":
        return Unit.name.desc()
    if sort_key == "new":
        return Unit.id.desc()
    return Unit.name.asc()

async def _pp_counts_for_units(session, unit_ids: list[int]) -> tuple[dict[int,int], dict[int,int]]:
    """
    خروجی: (child_count_map, admin_count_map)
    """
    if not unit_ids:
        return {}, {}

    child_counts = {}
    admin_counts = {}

    # children count
    q_child = (
        select(Unit.parent_id, func.count().label("c"))
        .where(Unit.parent_id.in_(unit_ids))
        .group_by(Unit.parent_id)
    )
    for pid, c in (await session.execute(q_child)).all():
        child_counts[pid] = c

    # admin count
    q_admin = (
        select(UnitAdmin.unit_id, func.count().label("c"))
        .where(UnitAdmin.unit_id.in_(unit_ids))
        .group_by(UnitAdmin.unit_id)
    )
    for uid, c in (await session.execute(q_admin)).all():
        admin_counts[uid] = c

    return child_counts, admin_counts

async def _pp_fetch_parents_page(session, parent_type: str, page: int, q: str | None, sort_key: str):
    """
    لیست والدهای مجاز (فقط نوع parent_type)، با جستجو و مرتب‌سازی و صفحه‌بندی.
    """
    base = select(Unit).where(Unit.type == parent_type)
    if q:
        like = f"%{q}%"
        base = base.where(func.lower(Unit.name).like(func.lower(like)))

    total = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar_one()

    rows = (await session.execute(
        base.order_by(_pp_order_clause(sort_key))
            .limit(PP_PAGE_SIZE)
            .offset(page * PP_PAGE_SIZE)
    )).scalars().all()

    # شمارنده‌ها
    ids = [u.id for u in rows]
    child_map, admin_map = await _pp_counts_for_units(session, ids)

    return rows, total, child_map, admin_map

def _pp_nav_kb(*, page: int, total: int, sort_key: str, q: str | None) -> list[list[InlineKeyboardButton]]:
    max_page = max(0, (total - 1) // PP_PAGE_SIZE)
    rows: list[list[InlineKeyboardButton]] = []

    # paging
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"uw:pp:list:{page-1}:{sort_key}:{q or ''}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"uw:pp:list:{page+1}:{sort_key}:{q or ''}"))
    if nav:
        rows.append(nav)

    # tools
    tools = [
        InlineKeyboardButton("مرتب‌سازی", callback_data=f"uw:pp:sort:{sort_key}"),
        InlineKeyboardButton("جستجو", callback_data="uw:pp:search"),
    ]
    if q:
        tools.append(InlineKeyboardButton("پاک‌کردن جستجو", callback_data=f"uw:pp:list:0:{sort_key}:"))
    tools.append(InlineKeyboardButton("بازگشت", callback_data="uw:pp:back"))
    rows.append(tools)

    return rows

async def _pp_render_parent_list(
    target_message,
    *,
    parent_type: str,
    page: int,
    q: str | None,
    sort_key: str,
    edit: bool,
    scope_root_id: int | None = None,
    is_super: bool = False,
):
    """
    رندر صفحه‌ی انتخاب والد با احترام به محدوده‌ی ادمین.
    """
    async with SessionLocal() as s:
        if not is_super and scope_root_id:
            rows, total, child_map, admin_map = await _pp_fetch_parents_page_scoped(
                s, parent_type, page, q, sort_key, scope_root_id
            )
        else:
            rows, total, child_map, admin_map = await _pp_fetch_parents_page(
                s, parent_type, page, q, sort_key
            )

    header = f"انتخاب والد (نوع مجاز: {parent_type}) | نتایج: {total}"
    meta = f"مرتب‌سازی: {sort_key} | صفحه: {page+1}/{max(1,(total+PP_PAGE_SIZE-1)//PP_PAGE_SIZE)}"
    if q:
        meta += f' | جستجو: "{q}"'

    text = header + "\n" + meta + "\n— یک مورد را انتخاب کنید —\n"

    kb_rows: list[list[InlineKeyboardButton]] = []
    for u in rows:
        cc = child_map.get(u.id, 0)
        ac = admin_map.get(u.id, 0)
        label = f"#{u.id} | نوع: {u.type} | نام: {u.name} | فرزند: {cc} | ادمین: {ac}"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"uw:pp:pick:{u.id}")])

    kb_rows += _pp_nav_kb(page=page, total=total, sort_key=sort_key, q=q)

    kb = InlineKeyboardMarkup(kb_rows)
    if edit:
        await target_message.edit_text(text, reply_markup=kb)
    else:
        await target_message.reply_text(text, reply_markup=kb)




async def _pp_fetch_parents_page_scoped(session, parent_type: str, page: int, q: str | None, sort_key: str, scope_root_id: int):
    """
    مثل _pp_fetch_parents_page اما والدها را به محدودهٔ scope محدود می‌کند.
    برای سادگی، همه را می‌خوانیم و در پایتون فیلتر می‌کنیم (عمق درخت کم است).
    """
    base = select(Unit).where(Unit.type == parent_type)
    if q:
        like = f"%{q}%"
        base = base.where(func.lower(Unit.name).like(func.lower(like)))

    all_rows = (await session.execute(base.order_by(_pp_order_clause(sort_key)))).scalars().all()

    scoped = []
    for u in all_rows:
        if await _unit_has_ancestor(session, u, scope_root_id):
            scoped.append(u)

    total = len(scoped)
    rows = scoped[page * PP_PAGE_SIZE: (page + 1) * PP_PAGE_SIZE]

    ids = [u.id for u in rows]
    child_map, admin_map = await _pp_counts_for_units(session, ids)
    return rows, total, child_map, admin_map


# ===================== Browse (Read-Only Unit List) =====================

# State های گفتگو (از 100 شروع می‌کنیم تا با ویزارد ساخت تداخل نکند)
UL_LIST, UL_SEARCH = range(100, 102)
UL_PAGE_SIZE = 8  # به جای BROWSE_PAGE_SIZE از این استفاده می‌کنیم
UL_SORTS = ("name_asc", "name_desc", "new")  # چرخه مرتب‌سازی
UL_TYPES = ("ALL", "COUNTRY", "OSTAN", "SHAHR", "HOZE", "PAYGAH")
# === Unit Admins Manager ===
UAM_PICK_UNIT, UAM_LIST, UAM_SEARCH, UAM_CONFIRM, UAM_ADD_ADMIN, UAM_ADD_ROLE, UAM_ADD_CONFIRM = range(300, 307)
UAM_PAGE_SIZE = 8

# تعیین نوع فرزند هر سطح برای مرور سلسله‌مراتبی
CHILD_TYPE_OF = {
    None:        "COUNTRY",  # در ریشه، لیست کشورها
    "COUNTRY":   "OSTAN",
    "OSTAN":     "SHAHR",
    "SHAHR":     "HOZE",
    "HOZE":      "PAYGAH",
    "PAYGAH":    None,       # پایین‌ترین سطح؛ دیگر زیرمجموعه ندارد
}

BROWSE_PAGE_SIZE = 8  # اندازه صفحه در مرور فقط-نمایش

async def _ul_fetch_page(session, *, parent_id: int | None, page: int, q: str | None,
                         type_filter: str, sort_key: str):
    # تعیین نوع فرزند بر مبنای مکان فعلی در درخت
    if parent_id is None:
        parent_type = None
    else:
        parent = await session.get(Unit, parent_id)
        if not parent:
            return [], 0, {}, {}
        parent_type = parent.type

    child_type_default = CHILD_TYPE_OF.get(parent_type)
    effective_type = child_type_default
    if type_filter != "ALL":
        effective_type = type_filter

    if effective_type is None:
        return [], 0, {}, {}

    # -------------------- منطق جدید --------------------
    if q:  # حالت جستجو
        if parent_id is None:
            # در ریشه، جستجو سراسری
            if type_filter == "ALL":
                base = select(Unit)  # همه انواع، بدون محدودیت والد
            else:
                base = select(Unit).where(Unit.type == effective_type)
        else:
            # داخل یک والد، فقط فرزندان همان والد
            base = select(Unit).where(Unit.parent_id == parent_id, Unit.type == effective_type)
    else:  # مرور عادی (بدون جستجو)
        if parent_id is None:
            if type_filter == "ALL":
                # رفتار قبلی: فقط ریشه‌ها (کشورها)
                base = select(Unit).where(Unit.parent_id.is_(None), Unit.type == effective_type)
            else:
                # نمایش سراسری همه‌ی واحدهای این نوع (مثلاً همه‌ی استان‌ها)
                base = select(Unit).where(Unit.type == effective_type)
        else:
            base = select(Unit).where(Unit.parent_id == parent_id, Unit.type == effective_type)
    # ---------------------------------------------------

    if q:
        like = f"%{q}%"
        base = base.where(func.lower(Unit.name).like(func.lower(like)))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (await session.execute(
        base.order_by(_ul_order_clause(sort_key))
            .limit(UL_PAGE_SIZE)
            .offset(page * UL_PAGE_SIZE)
    )).scalars().all()

    ids = [u.id for u in rows]
    child_map, admin_map = await _ul_counts_for_units(session, ids)
    return rows, total, child_map, admin_map

async def _build_breadcrumb(session, parent_id: int | None, scope_root: int | None = None) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    chain = []

    # اگر ریشهٔ دامنه مشخص است، از آنجا به بالا نرویم
    stop_id = scope_root  # None = اجازه تا ریشهٔ واقعی

    curr_id = parent_id
    while curr_id is not None:
        u = await session.get(Unit, curr_id)
        if not u:
            break
        chain.append((f"{UNIT_TYPE_LABELS.get(u.type, u.type)} {u.name}", f"ul:crumb:{u.id}"))
        if stop_id is not None and curr_id == stop_id:
            # به ریشهٔ دامنه رسیدیم
            curr_id = None
        else:
            curr_id = u.parent_id

    # اگر scope_root نداریم (سوپر) یک «کشورها» به‌عنوان خانه بگذار
    if scope_root is None:
        items.append(("📍 کشورها", "ul:crumb:root"))
    else:
        # تیتر ریشهٔ دامنه را خودِ واحد نمایش بدهیم
        ru = await session.get(Unit, scope_root)
        if ru:
            items.append((f"📍 {UNIT_TYPE_LABELS.get(ru.type, ru.type)} {ru.name}", f"ul:crumb:{ru.id}"))

    for label, cb in reversed(chain):
        items.append((label, cb))

    return items

def _list_nav_kb(page: int, total: int, at_root: bool, has_search: bool) -> InlineKeyboardMarkup:
    """
    دکمه‌های پایین صفحه: صفحه‌بندی/جستجو/پاک‌کردن/ریشه/بازگشت
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # ردیف صفحه‌بندی
    max_page = max(0, (total - 1) // BROWSE_PAGE_SIZE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"ul:page:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"ul:page:{page+1}"))
    if nav:
        buttons.append(nav)

    # ردیف ابزار
    tools = [InlineKeyboardButton("🔍 جستجو", callback_data="ul:search")]
    if has_search:
        tools.append(InlineKeyboardButton("🔄 پاک‌کردن", callback_data="ul:clear"))
    if not at_root:
        tools.append(InlineKeyboardButton("🔝 ریشه", callback_data="ul:root"))
    tools.append(InlineKeyboardButton("◀️ بازگشت", callback_data="sa:unit"))
    buttons.append(tools)

    return InlineKeyboardMarkup(buttons)

async def _render_unit_list(target_message, context: ContextTypes.DEFAULT_TYPE, *,
                            parent_id: int | None, page: int, q: str | None,
                            sort_key: str, type_filter: str, edit: bool):
    scope_root = context.user_data.get("ul_scope_root")  # None=سوپر، عدد=ادمین

    async with SessionLocal() as s:
        rows, total, child_map, admin_map = await _ul_fetch_page(
            s, parent_id=parent_id, page=page, q=q,
            type_filter=type_filter, sort_key=sort_key
        )
        breadcrumb = await _build_breadcrumb(s, parent_id, scope_root)

    bc_text = " › ".join([lbl for (lbl, _) in breadcrumb])
    summary = f"نتایج: {total}"
    if q: summary += f' | جستجو: "{q}"'
    summary += f" | نوع: {type_filter} | مرتب‌سازی: {sort_key}"
    if total > 0:
        max_page = max(1, (total + UL_PAGE_SIZE - 1) // UL_PAGE_SIZE)
        summary += f" | صفحه {page+1}/{max_page}"

    lines = [bc_text, summary, "— روی نام بزنید تا زیرمجموعه را ببینید —", ""]
    text = "\n".join(lines)

    kb_rows: list[list[InlineKeyboardButton]] = []
    bc_row = [InlineKeyboardButton(lbl, callback_data=cb) for (lbl, cb) in breadcrumb]
    if bc_row:
        kb_rows.append(bc_row)

    for u in rows:
        cc = child_map.get(u.id, 0)
        ac = admin_map.get(u.id, 0)
        label = f"#{u.id} | {_type_label_no_emoji(u.type)} {u.name} | فرزند: {cc} | ادمین: {ac}"
        kb_rows.append([
            InlineKeyboardButton(label, callback_data=f"ul:enter:{u.id}"),
            InlineKeyboardButton("👤 ادمین‌ها", callback_data=f"uam:open:{u.id}")
        ])

    # ← تفاوت اینجاست: at_root را نسبت به دامنه بساز
    at_root = (parent_id is None) if scope_root is None else (parent_id == scope_root)
    nav = _ul_nav_kb(page=page, total=total, at_root=at_root, has_search=bool(q),
                     sort_key=sort_key, type_filter=type_filter)
    kb_rows += nav.inline_keyboard

    kb = InlineKeyboardMarkup(kb_rows)
    if edit: await target_message.edit_text(text, reply_markup=kb)
    else:    await target_message.reply_text(text, reply_markup=kb)

async def ul_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        uid = update.effective_user.id
        if not await is_admin(s, uid):
            if update.message:
                return await update.message.reply_text("⛔️ شما ادمین نیستید.")
            elif update.callback_query:
                return await update.callback_query.answer("⛔️ شما ادمین نیستید.", show_alert=True)

        is_super = await is_superadmin(s, uid)
        root_id = None
        if not is_super:
            root_id = await get_primary_unit_for_admin(s, uid)
            if not root_id:
                # اگر واحد اصلی ندارد، یک پیام راهنما بده
                msg = "برای شما واحد اصلی ثبت نشده. از سوپرادمین بخواهید شما را به یک واحد وصل کند."
                if update.message:  return await update.message.reply_text(msg)
                else:               return await update.callback_query.edit_message_text(msg)

    # وضعیت مرور + محدوده
    context.user_data["ul_scope_root"] = root_id     # ← None برای سوپر، عدد برای ادمین
    context.user_data["ul_parent"]      = root_id    # ← شروع از ریشهٔ دامنه
    context.user_data["ul_page"]        = 0
    context.user_data["ul_q"]           = None
    context.user_data["ul_sort"]        = "name_asc"
    context.user_data["ul_type"]        = "ALL"

    target = update.message or update.callback_query.message
    await _render_unit_list(
        target, context,
        parent_id=root_id, page=0, q=None,
        sort_key="name_asc", type_filter="ALL", edit=False
    )
    return UL_LIST

async def ul_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qobj = update.callback_query
    await qobj.answer()
    data = qobj.data

    parent_id  = context.user_data.get("ul_parent")
    page       = int(context.user_data.get("ul_page", 0))
    qtext      = context.user_data.get("ul_q")
    sort_key   = context.user_data.get("ul_sort", "name_asc")
    type_filter= context.user_data.get("ul_type", "ALL")
    scope_root = context.user_data.get("ul_scope_root")

    # ورود به زیرمجموعه
    if data.startswith("ul:enter:"):
        pid = int(data.split(":")[2])
        context.user_data["ul_parent"] = pid
        context.user_data["ul_page"]   = 0
        context.user_data["ul_q"]      = None
        context.user_data["ul_type"]   = "ALL"
        await _render_unit_list(qobj.message, context,
                                parent_id=pid, page=0, q=None,
                                sort_key=sort_key, type_filter="ALL", edit=True)
        return UL_LIST

    # رفتن به ریشهٔ دامنه
    if data == "ul:root" or data == "ul:crumb:root":
        context.user_data["ul_parent"] = scope_root
        context.user_data["ul_page"]   = 0
        context.user_data["ul_q"]      = None
        await _render_unit_list(qobj.message, context,
                                parent_id=scope_root, page=0, q=None,
                                sort_key=sort_key, type_filter=type_filter, edit=True)
        return UL_LIST

    # دکمهٔ بازگشت به منوی اصلیِ نقش
    if data == "ul:back":
        async with SessionLocal() as s:
            if await is_superadmin(s, qobj.from_user.id):
                return await qobj.edit_message_text("مدیریت واحدها:", reply_markup=sa_units_menu())
            else:
                return await qobj.edit_message_text("مدیریت واحدها:", reply_markup=adm_units_menu())

    # پرش روی breadcrumb
    if data.startswith("ul:crumb:"):
        pid = int(data.split(":")[2])
        if scope_root is not None and pid == 0:
            pid = scope_root
        context.user_data["ul_parent"] = pid
        context.user_data["ul_page"]   = 0
        context.user_data["ul_q"]      = None
        await _render_unit_list(qobj.message, context,
                                parent_id=pid, page=0, q=None,
                                sort_key=sort_key, type_filter=type_filter, edit=True)
        return UL_LIST

    # صفحه‌بندی
    if data.startswith("ul:page:"):
        new_page = int(data.split(":")[2])
        context.user_data["ul_page"] = new_page
        await _render_unit_list(qobj.message, context,
                                parent_id=parent_id, page=new_page, q=qtext,
                                sort_key=sort_key, type_filter=type_filter, edit=True)
        return UL_LIST

    # جستجو/پاک‌کردن
    if data == "ul:search":
        await qobj.edit_message_text("عبارت جستجو را ارسال کنید:")
        return UL_SEARCH

    if data == "ul:clear":
        context.user_data["ul_q"]   = None
        context.user_data["ul_page"]= 0
        await _render_unit_list(qobj.message, context,
                                parent_id=parent_id, page=0, q=None,
                                sort_key=sort_key, type_filter=type_filter, edit=True)
        return UL_LIST

    # مرتب‌سازی
    if data.startswith("ul:sort:"):
        cur = data.split(":")[2]
        new_sort = _ul_next_sort(cur)
        context.user_data["ul_sort"] = new_sort
        context.user_data["ul_page"] = 0
        await _render_unit_list(qobj.message, context,
                                parent_id=parent_id, page=0, q=qtext,
                                sort_key=new_sort, type_filter=type_filter, edit=True)
        return UL_LIST

    # فیلتر نوع
    if data.startswith("ul:type:"):
        t = data.split(":")[2]
        if t in UL_TYPES:
            context.user_data["ul_type"] = t
            context.user_data["ul_page"] = 0
            await _render_unit_list(qobj.message, context,
                                    parent_id=parent_id, page=0, q=qtext,
                                    sort_key=sort_key, type_filter=t, edit=True)
        else:
            await qobj.answer("نوع نامعتبر", show_alert=True)
        return UL_LIST

async def ul_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    دریافت متن جستجو و بازگشت به لیست
    """
    query_text = (update.message.text or "").strip()
    context.user_data["ul_q"] = query_text if query_text else None
    context.user_data["ul_page"] = 0
    parent_id = context.user_data.get("ul_parent")
    sort_key = context.user_data.get("ul_sort", "name_asc")
    type_filter = context.user_data.get("ul_type", "ALL")

    await _render_unit_list(update.message, context,
        parent_id=parent_id, page=0, q=context.user_data["ul_q"],
        sort_key=sort_key, type_filter=type_filter, edit=False)
    return UL_LIST

def _ul_nav_kb(*, page: int, total: int, at_root: bool, has_search: bool,
               sort_key: str, type_filter: str) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    max_page = max(0, (total - 1) // UL_PAGE_SIZE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"ul:page:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"ul:page:{page+1}"))
    if nav:
        buttons.append(nav)

    tools = [InlineKeyboardButton("مرتب‌سازی", callback_data=f"ul:sort:{sort_key}"),
             InlineKeyboardButton("جستجو", callback_data="ul:search")]
    if has_search:
        tools.append(InlineKeyboardButton("پاک‌کردن جستجو", callback_data="ul:clear"))
    if not at_root:
        tools.append(InlineKeyboardButton("ریشه", callback_data="ul:root"))
    # ↓ این سطر عوض شد:
    tools.append(InlineKeyboardButton("بازگشت", callback_data="ul:back"))
    buttons.append(tools)

    # فیلتر نوع (همان قبلی)
    type_row1, type_row2 = [], []
    for t in UL_TYPES:
        label = f"[{t}]" if t == type_filter else t
        (type_row1 if len(type_row1) < 3 else type_row2).append(
            InlineKeyboardButton(label, callback_data=f"ul:type:{t}")
        )
    buttons.append(type_row1)
    buttons.append(type_row2)

    return InlineKeyboardMarkup(buttons)

def _ul_next_sort(cur: str) -> str:
    try:
        i = UL_SORTS.index(cur)
        return UL_SORTS[(i + 1) % len(UL_SORTS)]
    except ValueError:
        return "name_asc"

def _ul_order_clause(sort_key: str):
    if sort_key == "name_desc":
        return Unit.name.desc()
    if sort_key == "new":
        return Unit.id.desc()
    return Unit.name.asc()

def _type_label_no_emoji(t: str) -> str:
    return TEXT_TYPE_LABELS.get(t, t)

async def _ul_counts_for_units(session, unit_ids: list[int]) -> tuple[dict[int,int], dict[int,int]]:
    if not unit_ids:
        return {}, {}
    child_counts = {}
    admin_counts = {}
    q_child = (
        select(Unit.parent_id, func.count().label("c"))
        .where(Unit.parent_id.in_(unit_ids))
        .group_by(Unit.parent_id)
    )
    for pid, c in (await session.execute(q_child)).all():
        child_counts[pid] = c

    q_admin = (
        select(UnitAdmin.unit_id, func.count().label("c"))
        .where(UnitAdmin.unit_id.in_(unit_ids))
        .group_by(UnitAdmin.unit_id)
    )
    for uid, c in (await session.execute(q_admin)).all():
        admin_counts[uid] = c
    return child_counts, admin_counts


# ✅ مرور درخت/لیست واحدها (خواندنی)
def build_unit_list_conversation():
    async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await update.message.reply_text("❌ عملیات لغو شد.")
        elif update.callback_query:
            await update.callback_query.answer("❌ عملیات لغو شد.", show_alert=True)

    return ConversationHandler(
        entry_points=[
            CommandHandler("unit_list", ul_start),
            CallbackQueryHandler(ul_start, pattern=r"^(sa|adm):unit:list$"),
        ],
        states={
            UL_LIST: [
                CallbackQueryHandler(
                    ul_router,
                    pattern=r"^ul:(enter|crumb|page|search|clear|root|back|sort:[^:]+|type:(ALL|COUNTRY|OSTAN|SHAHR|HOZE|PAYGAH))(:.*)?$"
                ),
            ],
            UL_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ul_search_input)
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        allow_reentry=True,
    )


# ===================== Attach Admin to Unit – Wizard =====================

# [🔧 تغییر لازم] بالای فایل همین importها هست؛ چیزی اضافه لازم نیست.
# اگر جای دیگری فایل Admin تعریف شده، همین‌ها کافی است.

# --- State ها ---
UA_LANDING, UA_PICK_UNIT, UA_UNIT_SEARCH, UA_PICK_ADMIN, UA_ADMIN_SEARCH, UA_ROLE, UA_CONFIRM = range(200, 207)

# --- Context key برای داده‌های ویزارد ---
UA_CTX = "ua"  # context.user_data[UA_CTX] = {...}

def _ua_init_ctx(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[UA_CTX] = {
        "flow": "unit_first",     # یا "admin_first"
        "view": "tree",           # "tree" یا "list" برای انتخاب واحد
        # وضعیت انتخاب واحد
        "unit_parent": None,
        "unit_page": 0,
        "unit_q": None,
        "unit_sort": "name_asc",
        "unit_type": "ALL",       # فقط در نمای فهرستی
        "selected_unit_id": None,
        # وضعیت انتخاب ادمین
        "admin_page": 0,
        "admin_q": None,
        "admin_sort": "new",      # بر اساس admin_id desc
        "selected_admin_id": None,
        # نقش
        "role": "ASSISTANT",
    }

# ---------- Entry ----------
async def ua_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # کنترل دسترسی
    async with SessionLocal() as s:
        if not await is_admin(s, update.effective_user.id):
            if update.message:
                return await update.message.reply_text("⛔️ شما ادمین نیستید.")
            elif update.callback_query:
                return await update.callback_query.answer("⛔️ شما ادمین نیستید.", show_alert=True)

    _ua_init_ctx(context)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("اول واحد، بعد ادمین", callback_data="ua:flow:unit_first")],
        [InlineKeyboardButton("اول ادمین، بعد واحد", callback_data="ua:flow:admin_first")],
        [InlineKeyboardButton("لغو", callback_data="ua:cancel")],
    ])
    target = update.message or update.callback_query.message
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("اتصال ادمین به واحد — انتخاب ترتیب:", reply_markup=kb)
    else:
        await target.reply_text("اتصال ادمین به واحد — انتخاب ترتیب:", reply_markup=kb)
    return UA_LANDING

async def ua_landing_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    ctx = context.user_data[UA_CTX]

    if data == "ua:cancel":
        await q.edit_message_text("❌ عملیات لغو شد.")
        return ConversationHandler.END

    if data.startswith("ua:flow:"):
        flow = data.split(":")[2]
        ctx["flow"] = flow
        if flow == "unit_first":
            # شروع از انتخاب واحد (نمای درختی)
            ctx["view"] = "tree"
            return await _ua_render_unit_picker(q.message, context, edit=True)
        else:
            # شروع از انتخاب ادمین
            return await _ua_render_admin_picker(q.message, context, edit=True)

    return UA_LANDING


# ---------- انتخاب واحد: نمای درختی / فهرستی ----------
async def _ua_render_unit_picker(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    """
    با توجه به ctx['view'] یکی از رندرهای Tree/List را نشان می‌دهد.
    """
    ctx = context.user_data[UA_CTX]
    if ctx.get("view") == "list":
        return await _ua_render_unit_list(target_message, context, edit=edit)
    else:
        return await _ua_render_unit_tree(target_message, context, edit=edit)

async def _ua_render_unit_tree(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    """
    استفاده از helperهای مرور موجود: _ul_fetch_page, _build_breadcrumb, _ul_nav_kb
    - تفاوت: روی هر آیتم، انتخاب واحد (ua:pick_unit:<id>) انجام می‌شود.
    """
    ctx = context.user_data[UA_CTX]
    parent_id = ctx.get("unit_parent")
    page = int(ctx.get("unit_page", 0))
    q = ctx.get("unit_q")
    sort_key = ctx.get("unit_sort", "name_asc")

    async with SessionLocal() as s:
        rows, total, child_map, admin_map = await _ul_fetch_page(
            s,
            parent_id=parent_id,
            page=page,
            q=q,
            type_filter="ALL",  # درخت بر اساس CHILD_TYPE_OF حرکت می‌کند
            sort_key=sort_key
        )
        breadcrumb = await _build_breadcrumb(s, parent_id)

    bc_text = " › ".join([lbl for (lbl, _) in breadcrumb]) or "ریشه"
    summary = f"انتخاب واحد (درختی) — نتایج: {total}"
    if q: summary += f' | جستجو: "{q}"'
    summary += f" | مرتب‌سازی: {sort_key}"
    if total > 0:
        max_page = max(1, (total + UL_PAGE_SIZE - 1) // UL_PAGE_SIZE)
        summary += f" | صفحه {page+1}/{max_page}"

    header = "\n".join([bc_text, summary, "— روی یک مورد بزنید تا «انتخاب» شود —", ""])

    kb_rows: list[list[InlineKeyboardButton]] = []
    # breadcrumb کلیکی (پرش)
    bc_row = [InlineKeyboardButton(lbl, callback_data=cb.replace("ul:crumb", "ua:crumb")) for (lbl, cb) in breadcrumb]
    if bc_row:
        kb_rows.append(bc_row)

    for u in rows:
        cc = child_map.get(u.id, 0)
        ac = admin_map.get(u.id, 0)
        label = f"#{u.id} | {_type_label_no_emoji(u.type)} {u.name} | فرزند: {cc} | ادمین: {ac}"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"ua:pick_unit:{u.id}")])

    # ناوبری پایین + ابزار
    # صفحه‌بندی (ua:page:<n>)، جستجو، پاک‌کردن، مرتب‌سازی، ریشه، تغییر به فهرستی، بازگشت/لغو
    max_page = max(0, (total - 1) // UL_PAGE_SIZE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"ua:page:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"ua:page:{page+1}"))
    if nav:
        kb_rows.append(nav)

    tools = [
        InlineKeyboardButton("جستجو", callback_data="ua:search"),
        InlineKeyboardButton("مرتب‌سازی", callback_data=f"ua:sort:{sort_key}"),
    ]
    if q:
        tools.append(InlineKeyboardButton("پاک‌کردن جستجو", callback_data="ua:clear"))
    tools.append(InlineKeyboardButton("ریشه", callback_data="ua:root"))
    tools.append(InlineKeyboardButton("تغییر به فهرستی", callback_data="ua:view:list"))
    tools.append(InlineKeyboardButton("بازگشت", callback_data="ua:back_or_cancel"))
    kb_rows.append(tools)

    kb = InlineKeyboardMarkup(kb_rows)
    if edit:
        await target_message.edit_text(header, reply_markup=kb)
    else:
        await target_message.reply_text(header, reply_markup=kb)
    return UA_PICK_UNIT

async def _ua_render_unit_list(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    """
    نمای فهرستی: از helperهای موجود استفاده می‌کند (type_filter فعال).
    """
    ctx = context.user_data[UA_CTX]
    parent_id = ctx.get("unit_parent")  # در فهرستی معمولاً None
    page = int(ctx.get("unit_page", 0))
    q = ctx.get("unit_q")
    sort_key = ctx.get("unit_sort", "name_asc")
    type_filter = ctx.get("unit_type", "ALL")

    async with SessionLocal() as s:
        rows, total, child_map, admin_map = await _ul_fetch_page(
            s,
            parent_id=parent_id,
            page=page,
            q=q,
            type_filter=type_filter,
            sort_key=sort_key
        )
        breadcrumb = await _build_breadcrumb(s, parent_id)

    bc_text = " › ".join([lbl for (lbl, _) in breadcrumb]) or "ریشه"
    summary = f"انتخاب واحد (فهرستی) — نتایج: {total}"
    if q: summary += f' | جستجو: "{q}"'
    summary += f" | نوع: {type_filter} | مرتب‌سازی: {sort_key}"
    if total > 0:
        max_page = max(1, (total + UL_PAGE_SIZE - 1) // UL_PAGE_SIZE)
        summary += f" | صفحه {page+1}/{max_page}"

    header = "\n".join([bc_text, summary, "— یک واحد را انتخاب کنید —", ""])

    kb_rows: list[list[InlineKeyboardButton]] = []
    # breadcrumb برای هماهنگی
    bc_row = [InlineKeyboardButton(lbl, callback_data=cb.replace("ul:crumb", "ua:crumb")) for (lbl, cb) in breadcrumb]
    if bc_row:
        kb_rows.append(bc_row)

    for u in rows:
        cc = child_map.get(u.id, 0)
        ac = admin_map.get(u.id, 0)
        label = f"#{u.id} | {_type_label_no_emoji(u.type)} {u.name} | فرزند: {cc} | ادمین: {ac}"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"ua:pick_unit:{u.id}")])

    # ابزار پایین: صفحه‌بندی، مرتب‌سازی، جستجو، پاک‌کردن، نوع‌ها، سوییچ به درختی، بازگشت
    max_page = max(0, (total - 1) // UL_PAGE_SIZE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"ua:page:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"ua:page:{page+1}"))
    if nav:
        kb_rows.append(nav)

    tools = [InlineKeyboardButton("مرتب‌سازی", callback_data=f"ua:sort:{sort_key}"),
             InlineKeyboardButton("جستجو", callback_data="ua:search")]
    if q:
        tools.append(InlineKeyboardButton("پاک‌کردن جستجو", callback_data="ua:clear"))
    tools.append(InlineKeyboardButton("تغییر به درختی", callback_data="ua:view:tree"))
    kb_rows.append(tools)

    # فیلتر نوع (دو ردیف)
    type_row1, type_row2 = [], []
    for t in UL_TYPES:
        label = f"[{t}]" if t == type_filter else t
        btn = InlineKeyboardButton(label, callback_data=f"ua:type:{t}")
        (type_row1 if len(type_row1) < 3 else type_row2).append(btn)
    kb_rows.append(type_row1)
    kb_rows.append(type_row2)

    kb_rows.append([InlineKeyboardButton("بازگشت", callback_data="ua:back_or_cancel")])

    kb = InlineKeyboardMarkup(kb_rows)
    if edit:
        await target_message.edit_text(header, reply_markup=kb)
    else:
        await target_message.reply_text(header, reply_markup=kb)
    return UA_PICK_UNIT

async def ua_unit_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    ctx = context.user_data[UA_CTX]

    # تغییر نما
    if data == "ua:view:list":
        ctx["view"] = "list"
        ctx["unit_page"] = 0
        return await _ua_render_unit_picker(q.message, context, edit=True)
    if data == "ua:view:tree":
        ctx["view"] = "tree"
        ctx["unit_page"] = 0
        ctx["unit_parent"] = None
        return await _ua_render_unit_picker(q.message, context, edit=True)

    # ناوبری درختی/فهرستی مشترک
    if data.startswith("ua:page:"):
        ctx["unit_page"] = int(data.split(":")[2])
        return await _ua_render_unit_picker(q.message, context, edit=True)

    if data == "ua:search":
        await q.edit_message_text("عبارت جستجو را ارسال کنید:")
        return UA_UNIT_SEARCH

    if data == "ua:clear":
        ctx["unit_q"] = None
        ctx["unit_page"] = 0
        return await _ua_render_unit_picker(q.message, context, edit=True)

    if data.startswith("ua:sort:"):
        cur = data.split(":")[2]
        ctx["unit_sort"] = _ul_next_sort(cur)
        ctx["unit_page"] = 0
        return await _ua_render_unit_picker(q.message, context, edit=True)

    if data == "ua:root":
        ctx["unit_parent"] = None
        ctx["unit_page"] = 0
        ctx["unit_q"] = None
        return await _ua_render_unit_picker(q.message, context, edit=True)

    if data.startswith("ua:crumb:"):
        # الگوی قبلی ul:crumb:<id> را به ua:crumb:<id> تبدیل کرده‌ایم
        pid = int(data.split(":")[2])
        ctx["unit_parent"] = pid
        ctx["unit_page"] = 0
        ctx["unit_q"] = None
        return await _ua_render_unit_picker(q.message, context, edit=True)

    if data.startswith("ua:type:"):
        t = data.split(":")[2]
        if t in UL_TYPES:
            ctx["unit_type"] = t
            ctx["unit_page"] = 0
            return await _ua_render_unit_picker(q.message, context, edit=True)
        else:
            await q.answer("نوع نامعتبر", show_alert=True)
            return UA_PICK_UNIT

    if data == "ua:back_or_cancel":
        # برگرد به Landing
        return await ua_start(update, context)

    if data.startswith("ua:pick_unit:"):
        uid = int(data.split(":")[2])
        # بررسی صحت واحد
        async with SessionLocal() as s:
            u = await s.get(Unit, uid)
            if not u:
                await q.answer("واحد پیدا نشد.", show_alert=True)
                return UA_PICK_UNIT
        ctx["selected_unit_id"] = uid
        # برو مرحلهٔ ادمین
        return await _ua_render_admin_picker(q.message, context, edit=True)

    return UA_PICK_UNIT

async def ua_unit_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = context.user_data[UA_CTX]
    text = (update.message.text or "").strip()
    ctx["unit_q"] = text if text else None
    ctx["unit_page"] = 0
    return await _ua_render_unit_picker(update.message, context, edit=False)


# ---------- انتخاب ادمین ----------
# لیست ادمین‌ها + ورودی شناسه + فوروارد پیام

async def _ua_fetch_admins_page(session, *, page: int, q: str | None, sort_key: str):
    """
    sort_key: "new" (admin_id DESC) | "id_asc" | "id_desc"
    q: اگر رقم بود و طول >= 3 → فیلتر برابر/یا like ساده
    """
    base = select(Admin)
    # فیلتر
    if q and q.isdigit():
        # جستجوی دقیق (می‌تونی خواستی like کنی، ولی برای int بهتره دقیق)
        base = base.where(Admin.admin_id == int(q))

    # مرتب‌سازی
    if sort_key == "id_asc":
        base = base.order_by(Admin.admin_id.asc())
    elif sort_key == "id_desc":
        base = base.order_by(Admin.admin_id.desc())
    else:
        # new
        base = base.order_by(Admin.admin_id.desc())

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (await session.execute(
        base.limit(UL_PAGE_SIZE).offset(page * UL_PAGE_SIZE)
    )).scalars().all()

    # شمارنده: چند واحد متصل
    # select admin_id, count(*) from UnitAdmin group by admin_id
    ua_counts = {}
    if rows:
        ids = [r.admin_id for r in rows]
        q_ua = select(UnitAdmin.admin_id, func.count().label("c")).where(UnitAdmin.admin_id.in_(ids)).group_by(UnitAdmin.admin_id)
        for aid, c in (await session.execute(q_ua)).all():
            ua_counts[aid] = c

    return rows, total, ua_counts

async def _ua_render_admin_picker(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    ctx = context.user_data[UA_CTX]
    page = int(ctx.get("admin_page", 0))
    q = ctx.get("admin_q")
    sort_key = ctx.get("admin_sort", "new")

    # خلاصه انتخاب واحد
    unit_line = "واحد: —"
    if ctx.get("selected_unit_id"):
        async with SessionLocal() as s:
            u = await s.get(Unit, ctx["selected_unit_id"])
            if u:
                unit_line = f"واحد: #{u.id} | {_type_label_no_emoji(u.type)} {u.name}"

    async with SessionLocal() as s:
        rows, total, ua_counts = await _ua_fetch_admins_page(s, page=page, q=q, sort_key=sort_key)

    header = f"انتخاب ادمین — نتایج: {total}"
    if q: header += f' | جستجو: "{q}"'
    header += f" | مرتب‌سازی: {sort_key}"
    if total > 0:
        max_page = max(1, (total + UL_PAGE_SIZE - 1) // UL_PAGE_SIZE)
        header += f" | صفحه {page+1}/{max_page}"
    text = unit_line + "\n" + header + "\n— یکی را انتخاب کنید یا شناسه بفرستید —\n"

    kb_rows: list[list[InlineKeyboardButton]] = []

    # ردیف ورودی‌ها
    kb_rows.append([InlineKeyboardButton("ارسال شناسهٔ عددی", callback_data="ua:admin:by_id"),
                    InlineKeyboardButton("فوروارد پیام از کاربر", callback_data="ua:admin:by_fwd")])

    # لیست ادمین‌های موجود
    for a in rows:
        cnt = ua_counts.get(a.admin_id, 0)
        kb_rows.append([InlineKeyboardButton(f"#{a.admin_id} | واحدهای متصل: {cnt}", callback_data=f"ua:pick_admin:{a.admin_id}")])

    # ناوبری و ابزار
    max_page = max(0, (total - 1) // UL_PAGE_SIZE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"ua:apage:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"ua:apage:{page+1}"))
    if nav:
        kb_rows.append(nav)

    tools = [InlineKeyboardButton("جستجو", callback_data="ua:asearch"),
             InlineKeyboardButton("مرتب‌سازی", callback_data=f"ua:asort:{sort_key}"),
             InlineKeyboardButton("بازگشت", callback_data="ua:back_to_unit")]
    kb_rows.append(tools)

    kb = InlineKeyboardMarkup(kb_rows)
    if edit:
        await target_message.edit_text(text, reply_markup=kb)
    else:
        await target_message.reply_text(text, reply_markup=kb)
    return UA_PICK_ADMIN

async def ua_admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    ctx = context.user_data[UA_CTX]

    if data == "ua:back_to_unit":
        return await _ua_render_unit_picker(q.message, context, edit=True)

    if data.startswith("ua:apage:"):
        ctx["admin_page"] = int(data.split(":")[2])
        return await _ua_render_admin_picker(q.message, context, edit=True)

    if data == "ua:asearch":
        await q.edit_message_text("جستجوی ادمین: شناسهٔ عددی را بفرستید (مثلاً 123456789):")
        return UA_ADMIN_SEARCH

    if data.startswith("ua:asort:"):
        cur = data.split(":")[2]
        # چرخه ساده: new -> id_asc -> id_desc -> new
        order = ("new", "id_asc", "id_desc")
        try:
            i = order.index(cur)
            ctx["admin_sort"] = order[(i+1)%len(order)]
        except ValueError:
            ctx["admin_sort"] = "new"
        ctx["admin_page"] = 0
        return await _ua_render_admin_picker(q.message, context, edit=True)

    if data == "ua:admin:by_id":
        await q.edit_message_text("شناسهٔ عددی تلگرام ادمین را ارسال کنید:")
        return UA_ADMIN_SEARCH

    if data == "ua:admin:by_fwd":
        await q.edit_message_text("یک پیام از کاربرِ موردنظر فوروارد کنید (Forward):")
        # همان state دریافت متن/پیام را استفاده می‌کنیم
        return UA_ADMIN_SEARCH

    if data.startswith("ua:pick_admin:"):
        aid = int(data.split(":")[2])
        ctx["selected_admin_id"] = aid
        return await _ua_render_role_picker(q.message, context, edit=True)

    return UA_PICK_ADMIN

async def ua_admin_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    هم برای ورودی متن (id) و هم پیام فورواردی کار می‌کند.
    """
    ctx = context.user_data[UA_CTX]
    aid = None

    # پیام فورواردی؟
    fwd = getattr(update.message, "forward_from", None)
    if fwd and getattr(fwd, "id", None):
        aid = int(fwd.id)
    else:
        # تلاش برای parse عدد
        txt = (update.message.text or "").strip()
        if txt.isdigit():
            aid = int(txt)

    if not aid:
        await update.message.reply_text("شناسه معتبر پیدا نشد. دوباره تلاش کنید یا یک پیام فوروارد کنید.")
        return UA_ADMIN_SEARCH

    # اگر Admin وجود ندارد، ثبت اولیه کنیم (نقش سیستمی پیش‌فرض: L1)
    async with SessionLocal() as s:
        adm = await s.get(Admin, aid)
        if not adm:
            s.add(Admin(admin_id=aid, role="L1"))
            await s.commit()

    ctx["selected_admin_id"] = aid
    return await _ua_render_role_picker(update.message, context, edit=False)


# ---------- نقش ----------
async def _ua_render_role_picker(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    ctx = context.user_data[UA_CTX]
    uid = ctx.get("selected_unit_id")
    aid = ctx.get("selected_admin_id")
    if not uid or not aid:
        # بازگشت ایمن
        return await _ua_render_unit_picker(target_message, context, edit=edit)

    # خلاصه‌ها
    async with SessionLocal() as s:
        u = await s.get(Unit, uid)
    unit_line = f"واحد: #{u.id} | {_type_label_no_emoji(u.type)} {u.name}" if u else f"واحد: #{uid}"

    text = unit_line + f"\nادمین: #{aid}\n\nنقش را انتخاب کنید:"
    role = ctx.get("role", "ASSISTANT")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(("✔ " if role=="OWNER" else "")+"OWNER", callback_data="ua:role:OWNER")],
        [InlineKeyboardButton(("✔ " if role=="ASSISTANT" else "")+"ASSISTANT", callback_data="ua:role:ASSISTANT")],
        [InlineKeyboardButton("ادامه", callback_data="ua:confirm"),
         InlineKeyboardButton("بازگشت", callback_data="ua:back_to_admin")],
    ])

    if edit:
        await target_message.edit_text(text, reply_markup=kb)
    else:
        await target_message.reply_text(text, reply_markup=kb)
    return UA_ROLE

async def ua_role_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    ctx = context.user_data[UA_CTX]

    if data == "ua:back_to_admin":
        return await _ua_render_admin_picker(q.message, context, edit=True)

    if data.startswith("ua:role:"):
        r = data.split(":")[2]
        if r in {"OWNER", "ASSISTANT"}:
            ctx["role"] = r
            # فقط رفرش همین صفحه با تیک
            return await _ua_render_role_picker(q.message, context, edit=True)
        else:
            await q.answer("نقش نامعتبر", show_alert=True)
            return UA_ROLE

    if data == "ua:confirm":
        return await _ua_render_confirm(q.message, context, edit=True)

    return UA_ROLE


# ---------- تأیید ----------
async def _ua_render_confirm(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    ctx = context.user_data[UA_CTX]
    uid = ctx.get("selected_unit_id")
    aid = ctx.get("selected_admin_id")
    role = ctx.get("role", "ASSISTANT")

    # اطلاعات واحد برای نمایش
    async with SessionLocal() as s:
        u = await s.get(Unit, uid)
    unit_line = f"واحد: #{u.id} | {_type_label_no_emoji(u.type)} {u.name}" if u else f"واحد: #{uid}"

    # آیا اتصال موجود است؟
    async with SessionLocal() as s:
        existing = await s.get(UnitAdmin, {"unit_id": uid, "admin_id": aid})
    exists_text = "⚠️ اتصال موجود است؛ با تأیید، نقش به‌روزرسانی می‌شود." if existing else "اتصال جدید ثبت می‌شود."

    text = f"{unit_line}\nادمین: #{aid}\nنقش: {role}\n\n{exists_text}\n\nتأیید می‌کنید؟"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ثبت/به‌روزرسانی اتصال", callback_data="ua:save")],
        [InlineKeyboardButton("بازگشت", callback_data="ua:back_to_role"),
         InlineKeyboardButton("لغو", callback_data="ua:cancel")],
    ])

    if edit:
        await target_message.edit_text(text, reply_markup=kb)
    else:
        await target_message.reply_text(text, reply_markup=kb)
    return UA_CONFIRM


async def ua_confirm_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    ctx = context.user_data[UA_CTX]
    uid = ctx.get("selected_unit_id")
    aid = ctx.get("selected_admin_id")
    role = ctx.get("role", "ASSISTANT")

    if data == "ua:back_to_role":
        return await _ua_render_role_picker(q.message, context, edit=True)
    if data == "ua:cancel":
        await q.edit_message_text("❌ عملیات لغو شد.")
        return ConversationHandler.END
    if data == "ua:save":
        async with SessionLocal() as s:
            u = await s.get(Unit, uid)
            if not u:
                await q.edit_message_text("❗️ واحد در دسترس نیست. دوباره تلاش کنید.")
                return ConversationHandler.END

            adm = await s.get(Admin, aid)
            if not adm:
                s.add(Admin(admin_id=aid, role="L1"))
                await s.flush()

            existing = await s.get(UnitAdmin, {"unit_id": uid, "admin_id": aid})
            if existing:
                existing.role = role
            else:
                s.add(UnitAdmin(unit_id=uid, admin_id=aid, role=role))
            await s.commit()

        await q.edit_message_text(f"✅ اتصال انجام شد: ادمین #{aid} → واحد #{uid} با نقش {role}")
        return ConversationHandler.END

    return UA_CONFIRM


# -------------------- ConversationHandler --------------------
# ✅ اتصال ادمین به واحد (ویزارد)
def build_unit_attach_conversation():
    async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await update.message.reply_text("❌ عملیات لغو شد.")
        elif update.callback_query:
            await update.callback_query.answer("❌ عملیات لغو شد.", show_alert=True)

    return ConversationHandler(
        entry_points=[
            CommandHandler("unit_attach_wizard", ua_start),
            # هم برای سوپرادمین هم ادمین
            CallbackQueryHandler(ua_start, pattern=r"^(sa|adm):unit:attach$"),
        ],
        states={
            UA_LANDING: [
                CallbackQueryHandler(ua_landing_router, pattern=r"^ua:(flow:(unit_first|admin_first)|cancel)$")
            ],
            UA_PICK_UNIT: [
                CallbackQueryHandler(
                    ua_unit_router,
                    pattern=r"^ua:(view:(tree|list)|page:\d+|search|clear|sort:[^:]+|root|crumb:\d+|type:(ALL|COUNTRY|OSTAN|SHAHR|HOZE|PAYGAH)|back_or_cancel|pick_unit:\d+)$"
                ),
            ],
            UA_UNIT_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ua_unit_search_input),
            ],
            UA_PICK_ADMIN: [
                CallbackQueryHandler(
                    ua_admin_router,
                    pattern=r"^ua:(back_to_unit|apage:\d+|asearch|asort:(new|id_asc|id_desc)|admin:(by_id|by_fwd)|pick_admin:\d+)$"
                ),
            ],
            UA_ADMIN_SEARCH: [
                MessageHandler(filters.ALL & ~filters.COMMAND, ua_admin_search_input),
            ],
            UA_ROLE: [
                CallbackQueryHandler(ua_role_router, pattern=r"^ua:(back_to_admin|role:(OWNER|ASSISTANT)|confirm)$")
            ],
            UA_CONFIRM: [
                CallbackQueryHandler(ua_confirm_router, pattern=r"^ua:(back_to_role|cancel|save)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        allow_reentry=True,
    )


# ✅ مدیر ادمین‌های یک واحد (لیست/تغییر نقش/حذف/افزودن)
def build_unit_admins_manager_conversation():
    async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await update.message.reply_text("❌ عملیات لغو شد.")
        elif update.callback_query:
            await update.callback_query.answer("❌ عملیات لغو شد.", show_alert=True)

    return ConversationHandler(
        entry_points=[
            CommandHandler("unit_admins", uam_start),
            CallbackQueryHandler(uam_start, pattern=r"^(sa|adm):unit:admins$"),
            CallbackQueryHandler(uam_open_direct, pattern=r"^uam:open:\d+$"),
        ],
        states={
            UAM_PICK_UNIT: [
                CallbackQueryHandler(
                    uam_unit_router,
                    pattern=r"^uam:(view:(tree|list)|page:\d+|search|clear|sort:[^:]+|root|crumb:\d+|type:(ALL|COUNTRY|OSTAN|SHAHR|HOZE|PAYGAH)|pick_unit:\d+|back)$"
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, uam_unit_search_input),
            ],
            UAM_LIST: [
                CallbackQueryHandler(
                    uam_list_router,
                    pattern=r"^uam:(add|\d+:(role:(OWNER|ASSISTANT)|del)|page:\d+|back_to_units)$"
                ),
            ],
            UAM_CONFIRM: [
                CallbackQueryHandler(uam_confirm_router, pattern=r"^uam:(confirm:(role|del):\d+:\d+:(OWNER|ASSISTANT)|cancel)$")
            ],
            UAM_ADD_ADMIN: [
                MessageHandler(filters.ALL & ~filters.COMMAND, uam_add_admin_input),
                CallbackQueryHandler(uam_list_router, pattern=r"^uam:cancel$"),
            ],
            UAM_ADD_ROLE: [
                CallbackQueryHandler(uam_add_role_router, pattern=r"^uam:add:(role:(OWNER|ASSISTANT)|back)$"),
            ],
            UAM_ADD_CONFIRM: [
                CallbackQueryHandler(uam_add_confirm_router, pattern=r"^uam:(add:confirm|cancel)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        allow_reentry=True,
    )


async def uam_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # دسترسی: هر ادمینی که به هر نحو اجازه دارد (فعلاً همان is_admin)
    async with SessionLocal() as s:
        if not await is_admin(s, update.effective_user.id):
            if update.message:
                return await update.message.reply_text("⛔️ شما ادمین نیستید.")
            else:
                return await update.callback_query.answer("⛔️ شما ادمین نیستید.", show_alert=True)

    # state انتخاب واحد (مثل UA، ولی ساده‌تر)
    ctx = context.user_data
    ctx["uam"] = {
        "view": "tree",
        "unit_parent": None,
        "unit_page": 0,
        "unit_q": None,
        "unit_sort": "name_asc",
        "unit_type": "ALL",
        "selected_unit_id": None,
        "list_page": 0,
    }

    target = update.message or update.callback_query.message
    if update.callback_query:
        await update.callback_query.answer()
    return await _uam_render_unit_picker(target, context, edit=bool(update.callback_query))


async def uam_open_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = int(q.data.split(":")[2])
    context.user_data["uam"] = {
        "view": "tree",
        "unit_parent": None,
        "unit_page": 0,
        "unit_q": None,
        "unit_sort": "name_asc",
        "unit_type": "ALL",
        "selected_unit_id": uid,
        "list_page": 0,
    }
    return await _uam_render_admins(q.message, context, edit=True)


async def _uam_render_unit_picker(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    ctx = context.user_data["uam"]
    parent_id = ctx["unit_parent"]; page = ctx["unit_page"]
    q = ctx["unit_q"]; sort_key = ctx["unit_sort"]; type_filter = ctx["unit_type"]

    async with SessionLocal() as s:
        rows, total, child_map, admin_map = await _ul_fetch_page(
            s, parent_id=parent_id, page=page, q=q, type_filter=type_filter, sort_key=sort_key
        )
        breadcrumb = await _build_breadcrumb(s, parent_id)

    bc_text = " › ".join([lbl for (lbl, _) in breadcrumb]) or "ریشه"
    header = f"{bc_text}\nانتخاب واحد برای مدیریت ادمین‌ها | نتایج: {total}"
    if q: header += f' | جستجو: "{q}"'
    header += f" | مرتب‌سازی: {sort_key}"

    kb_rows = []
    if breadcrumb:
        kb_rows.append([InlineKeyboardButton(lbl, callback_data=cb.replace("ul:crumb", "uam:crumb")) for (lbl, cb) in breadcrumb])

    for u in rows:
        cc = child_map.get(u.id, 0); ac = admin_map.get(u.id, 0)
        label = f"#{u.id} | {TEXT_TYPE_LABELS.get(u.type,u.type)} {u.name} | فرزند: {cc} | ادمین: {ac}"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"uam:pick_unit:{u.id}")])

    # ناوبری/ابزار
    max_page = max(0, (total - 1)//UL_PAGE_SIZE)
    nav = []
    if page>0: nav.append(InlineKeyboardButton("قبلی", callback_data=f"uam:page:{page-1}"))
    if page<max_page: nav.append(InlineKeyboardButton("بعدی", callback_data=f"uam:page:{page+1}"))
    if nav: kb_rows.append(nav)

    tools = [InlineKeyboardButton("جستجو", callback_data="uam:search"),
             InlineKeyboardButton("مرتب‌سازی", callback_data=f"uam:sort:{sort_key}")]
    if q: tools.append(InlineKeyboardButton("پاک‌کردن جستجو", callback_data="uam:clear"))
    tools.append(InlineKeyboardButton("بازگشت", callback_data="uam:back"))
    kb_rows.append(tools)

    kb = InlineKeyboardMarkup(kb_rows)
    if edit: await target_message.edit_text(header, reply_markup=kb)
    else:    await target_message.reply_text(header, reply_markup=kb)
    return UAM_PICK_UNIT


async def uam_unit_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data; ctx = context.user_data["uam"]

    if data == "uam:back":
        await q.edit_message_text("❌ عملیات لغو شد.")
        return ConversationHandler.END

    if data.startswith("uam:view:"):
        ctx["view"] = data.split(":")[2]  # فعلاً فقط tree را داریم
        ctx["unit_page"]=0; ctx["unit_parent"]=None
        return await _uam_render_unit_picker(q.message, context, edit=True)

    if data.startswith("uam:page:"):
        ctx["unit_page"] = int(data.split(":")[2])
        return await _uam_render_unit_picker(q.message, context, edit=True)

    if data == "uam:search":
        await q.edit_message_text("عبارت جستجو را ارسال کنید:")
        return UAM_SEARCH
    if data == "uam:clear":
        ctx["unit_q"]=None; ctx["unit_page"]=0
        return await _uam_render_unit_picker(q.message, context, edit=True)

    if data.startswith("uam:sort:"):
        ctx["unit_sort"] = _ul_next_sort(data.split(":")[2]); ctx["unit_page"]=0
        return await _uam_render_unit_picker(q.message, context, edit=True)

    if data == "uam:root":
        ctx["unit_parent"]=None; ctx["unit_page"]=0; ctx["unit_q"]=None
        return await _uam_render_unit_picker(q.message, context, edit=True)

    if data.startswith("uam:crumb:"):
        pid = int(data.split(":")[2])
        ctx["unit_parent"]=pid; ctx["unit_page"]=0; ctx["unit_q"]=None
        return await _uam_render_unit_picker(q.message, context, edit=True)

    if data.startswith("uam:type:"):
        t = data.split(":")[2]
        if t in UL_TYPES:
            ctx["unit_type"]=t; ctx["unit_page"]=0
            return await _uam_render_unit_picker(q.message, context, edit=True)
        return UAM_PICK_UNIT

    if data.startswith("uam:pick_unit:"):
        uid = int(data.split(":")[2])
        async with SessionLocal() as s:
            if not await s.get(Unit, uid):
                await q.answer("واحد پیدا نشد.", show_alert=True); return UAM_PICK_UNIT
        ctx["selected_unit_id"]=uid; ctx["list_page"]=0
        return await _uam_render_admins(q.message, context, edit=True)

    return UAM_PICK_UNIT

async def uam_unit_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = context.user_data["uam"]
    txt = (update.message.text or "").strip()
    ctx["unit_q"] = txt if txt else None; ctx["unit_page"]=0
    return await _uam_render_unit_picker(update.message, context, edit=False)


async def _uam_fetch_admins_for_unit(session, unit_id: int, *, page: int):
    base = select(UnitAdmin).where(UnitAdmin.unit_id == unit_id)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(
        base.order_by(UnitAdmin.role.desc(), UnitAdmin.admin_id.asc())
            .limit(UAM_PAGE_SIZE).offset(page * UAM_PAGE_SIZE)
    )).scalars().all()

    # شمارنده OWNERها برای محافظت از حذف/تنزل آخرین OWNER
    owners_count = (await session.execute(
        select(func.count()).select_from(UnitAdmin).where(UnitAdmin.unit_id==unit_id, UnitAdmin.role=="OWNER")
    )).scalar_one()

    # نام/نوع واحد
    u = await session.get(Unit, unit_id)
    return rows, total, owners_count, u



async def _uam_render_admins(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    ctx = context.user_data["uam"]; uid = ctx["selected_unit_id"]; page = ctx["list_page"]

    async with SessionLocal() as s:
        rows, total, owners_count, u = await _uam_fetch_admins_for_unit(s, uid, page=page)

    header = f"👤 ادمین‌های واحد #{uid} | {TEXT_TYPE_LABELS.get(u.type,u.type)} {u.name}\nنتایج: {total}"
    kb_rows = []

    # دکمه افزودن (باز کردن ویزارد اتصال با Prefill این واحد)
    kb_rows.append([InlineKeyboardButton("➕ افزودن ادمین به این واحد", callback_data="uam:add")])

    # لیست ادمین‌ها
    for ua in rows:
        role = ua.role
        aid = ua.admin_id
        # دکمه‌های عمل روی هر ردیف
        actions = [
            InlineKeyboardButton("تبدیل به OWNER" if role!="OWNER" else "تبدیل به ASSISTANT",
                                 callback_data=f"uam:{aid}:role:{'OWNER' if role!='OWNER' else 'ASSISTANT'}"),
            InlineKeyboardButton("حذف", callback_data=f"uam:{aid}:del")
        ]
        kb_rows.append([InlineKeyboardButton(f"#{aid} | نقش: {role}", callback_data="noop")])
        kb_rows.append(actions)

    # صفحه‌بندی
    max_page = max(0, (total - 1)//UAM_PAGE_SIZE)
    nav=[]
    if page>0: nav.append(InlineKeyboardButton("قبلی", callback_data=f"uam:page:{page-1}"))
    if page<max_page: nav.append(InlineKeyboardButton("بعدی", callback_data=f"uam:page:{page+1}"))
    if nav: kb_rows.append(nav)

    kb_rows.append([InlineKeyboardButton("◀️ انتخاب واحد دیگر", callback_data="uam:back_to_units")])

    kb = InlineKeyboardMarkup(kb_rows)
    if edit: await target_message.edit_text(header, reply_markup=kb)
    else:    await target_message.reply_text(header, reply_markup=kb)
    return UAM_LIST


async def uam_list_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data; ctx = context.user_data["uam"]; uid = ctx["selected_unit_id"]

    if data == "uam:add":
        ctx["adding_admin_id"] = None
        ctx["adding_role"] = "ASSISTANT"
        await q.edit_message_text(
            "شناسه عددی ادمین را بفرستید یا یک پیام از او فوروارد کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو", callback_data="uam:cancel")]])
        )
        return UAM_ADD_ADMIN

    if data == "uam:back_to_units":
        return await _uam_render_unit_picker(q.message, context, edit=True)

    if data.startswith("uam:page:"):
        ctx["list_page"] = int(data.split(":")[2])
        return await _uam_render_admins(q.message, context, edit=True)

    # تغییر نقش یا حذف
    # الگو: uam:{aid}:role:OWNER  |  uam:{aid}:del
    parts = data.split(":")
    if len(parts)>=3 and parts[0]=="uam" and parts[1].isdigit():
        aid = int(parts[1])
        action = parts[2]
        if action == "role" and len(parts)==4:
            new_role = parts[3]
            # تأیید
            await q.edit_message_text(
                f"تغییر نقش ادمین #{aid} در واحد #{uid} به «{new_role}»؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("تایید", callback_data=f"uam:confirm:role:{uid}:{aid}:{new_role}")],
                    [InlineKeyboardButton("لغو", callback_data="uam:cancel")]
                ])
            )
            return UAM_CONFIRM
        if action == "del":
            # بررسی آخرین OWNER در مرحلهٔ ذخیره انجام می‌شود؛ اینجا فقط تأیید می‌گیریم
            await q.edit_message_text(
                f"حذف ادمین #{aid} از واحد #{uid}؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("تایید", callback_data=f"uam:confirm:del:{uid}:{aid}:ASSISTANT")],
                    [InlineKeyboardButton("لغو", callback_data="uam:cancel")]
                ])
            )
            return UAM_CONFIRM

    return UAM_LIST


async def uam_confirm_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data; ctx = context.user_data["uam"]

    if data == "uam:cancel":
        # برگرد به لیست ادمین‌ها
        return await _uam_render_admins(q.message, context, edit=True)

    # الگو: uam:confirm:(role|del):<uid>:<aid>:<role>
    _, _, kind, uid_s, aid_s, role = data.split(":")
    uid = int(uid_s); aid = int(aid_s)

    async with SessionLocal() as s:
        # شمارش OWNERها (برای محافظت)
        owners_count = (await s.execute(
            select(func.count()).select_from(UnitAdmin).where(UnitAdmin.unit_id==uid, UnitAdmin.role=="OWNER")
        )).scalar_one()

        ua = await s.get(UnitAdmin, {"unit_id": uid, "admin_id": aid})
        if not ua:
            await q.edit_message_text("رکورد اتصال یافت نشد.")
            return ConversationHandler.END

        if kind == "del":
            # منع حذف آخرین OWNER
            if ua.role == "OWNER" and owners_count <= 1:
                await q.edit_message_text("⛔️ نمی‌توان آخرین OWNER را حذف کرد. ابتدا یک OWNER دیگر تعیین کنید.")
                return ConversationHandler.END
            await s.delete(ua); await s.commit()
            await q.edit_message_text(f"✅ ادمین #{aid} از واحد #{uid} حذف شد.")
            return ConversationHandler.END

        if kind == "role":
            new_role = role
            if ua.role == "OWNER" and new_role != "OWNER" and owners_count <= 1:
                await q.edit_message_text("⛔️ نمی‌توان آخرین OWNER را تنزل داد. ابتدا OWNER دیگری تعیین کنید.")
                return ConversationHandler.END
            ua.role = new_role
            await s.commit()
            await q.edit_message_text(f"✅ نقش ادمین #{aid} در واحد #{uid} به «{new_role}» تغییر کرد.")
            return ConversationHandler.END

    return ConversationHandler.END


async def uam_add_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = context.user_data["uam"]
    aid = None

    fwd = getattr(update.message, "forward_from", None)
    if fwd and getattr(fwd, "id", None):
        aid = int(fwd.id)
    else:
        txt = (update.message.text or "").strip()
        if txt.isdigit():
            aid = int(txt)

    if not aid:
        await update.message.reply_text("شناسه معتبر نیست. دوباره عدد بفرست یا پیامِ کاربر را فوروارد کن.")
        return UAM_ADD_ADMIN

    # اگر Admin نبود، بساز
    async with SessionLocal() as s:
        adm = await s.get(Admin, aid)
        if not adm:
            s.add(Admin(admin_id=aid, role="L1"))
            await s.commit()

    ctx["adding_admin_id"] = aid

    # انتخاب نقش
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("OWNER", callback_data="uam:add:role:OWNER")],
        [InlineKeyboardButton("ASSISTANT", callback_data="uam:add:role:ASSISTANT")],
        [InlineKeyboardButton("بازگشت", callback_data="uam:add:back")]
    ])
    await update.message.reply_text(f"ادمین انتخاب شد: #{aid}\nنقش را انتخاب کنید:", reply_markup=kb)
    return UAM_ADD_ROLE


async def uam_add_role_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx = context.user_data["uam"]
    data = q.data

    if data == "uam:add:back":
        # برگرد به لیست همان واحد
        return await _uam_render_admins(q.message, context, edit=True)

    if data.startswith("uam:add:role:"):
        role = data.split(":")[3]
        if role not in {"OWNER","ASSISTANT"}:
            await q.answer("نقش نامعتبر", show_alert=True); return UAM_ADD_ROLE
        ctx["adding_role"] = role

        uid = ctx["selected_unit_id"]; aid = ctx["adding_admin_id"]
        text = f"واحد #{uid}\nادمین #{aid}\nنقش: {role}\n\nثبت شود؟"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("بله، ثبت کن", callback_data="uam:add:confirm")],
            [InlineKeyboardButton("لغو", callback_data="uam:cancel")]
        ])
        await q.edit_message_text(text, reply_markup=kb)
        return UAM_ADD_CONFIRM

    return UAM_ADD_ROLE


async def uam_add_confirm_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx = context.user_data["uam"]
    if q.data == "uam:cancel":
        return await _uam_render_admins(q.message, context, edit=True)

    if q.data == "uam:add:confirm":
        uid = ctx["selected_unit_id"]; aid = ctx["adding_admin_id"]; role = ctx.get("adding_role","ASSISTANT")
        async with SessionLocal() as s:
            u = await s.get(Unit, uid)
            if not u:
                await q.edit_message_text("❗️واحد نامعتبر است."); return ConversationHandler.END
            existing = await s.get(UnitAdmin, {"unit_id": uid, "admin_id": aid})
            if existing:
                existing.role = role
            else:
                s.add(UnitAdmin(unit_id=uid, admin_id=aid, role=role))
            await s.commit()
        await q.edit_message_text(f"✅ ادمین #{aid} با نقش {role} به واحد #{uid} متصل شد.")
        return ConversationHandler.END

    return UAM_ADD_CONFIRM
