# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, zipfile, pathlib
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from database import SessionLocal
from models import Campaign, Report, ReportItem,Unit
from crud import (
    is_admin, is_superadmin, list_campaigns_for_admin_units, get_campaign,
    update_campaign_field, delete_campaign, stats_for_campaign, platforms_from_json, share_scope,list_campaigns_for_admin_unit_tree
)
from utils import safe_answer
from keyboards import platforms_keyboard, PLATFORM_KEYS, PLATFORM_LABEL
from keyboards import UNIT_TYPE_LABELS
from sqlalchemy import select, func, or_


MAX = 4000

async def send_campaign_stats(q, cid, by_unit):
    from keyboards import UNIT_TYPE_LABELS, PLATFORM_LABEL  # مطمئن شو PLATFORM_LABEL ایمپورت شده

    lines = [f"📊 آمار کمپین #{cid}"]

    for (uid, uname, utype), items in by_unit.items():
        label = UNIT_TYPE_LABELS.get(utype, utype)
        lines.append(f"\n{label} {uname}:")
        for plat, cnt in items:
            plat_label = PLATFORM_LABEL.get(plat, plat) if plat else "نامشخص"   # ⬅️ تغییر اصلی
            lines.append(f"• {plat_label}: {cnt}")

    text = "\n".join(lines)
    if len(text) <= MAX:
        await q.message.reply_text(text)
    else:
        await q.message.reply_text(f"📊 آمار کمپین #{cid} (بخش 1)")
        chunk = []
        cur = 0
        for (uid, uname, utype), items in by_unit.items():
            label = UNIT_TYPE_LABELS.get(utype, utype)
            block = [f"\n{label} {uname}:"]
            for plat, cnt in items:
                plat_label = PLATFORM_LABEL.get(plat, plat) if plat else "نامشخص"  # ⬅️ همین‌جا هم
                block.append(f"• {plat_label}: {cnt}")
            block_text = "\n".join(block)
            if cur + len(block_text) > MAX:
                await q.message.reply_text("".join(chunk))
                chunk = []
                cur = 0
            chunk.append(block_text)
            cur += len(block_text)
        if chunk:
            await q.message.reply_text("".join(chunk))


DATA_DIR = pathlib.Path("storage").absolute()

# لیست کمپین‌ها (بدون ویزارد، state با user_data نگه می‌داریم)
CAMP_PAGE_SIZE = 8

def _fmt_platforms(c: Campaign) -> str:

    keys = platforms_from_json(c.platforms)
    return ", ".join(PLATFORM_LABEL.get(k, k) for k in keys) or "-"


async def _descendant_unit_ids(session, root_id: int) -> list[int]:
    out = []; queue = [root_id]
    while queue:
        cur = queue.pop(0)
        rows = (await session.execute(select(Unit.id).where(Unit.parent_id == cur))).all()
        kids = [x for (x,) in rows]
        out.extend(kids); queue.extend(kids)
    return out


async def _scope_for_admin(session, admin_id: int) -> tuple[bool, Optional[int], Optional[list[int]]]:
    """برمی‌گرداند (is_super, root_unit_id, allowed_unit_ids)"""
    if await is_superadmin(session, admin_id):
        return True, None, None
    # ریشهٔ دامنه ادمین
    from crud import get_primary_unit_for_admin
    root = await get_primary_unit_for_admin(session, admin_id)
    if not root:
        return False, None, []
    ids = [root] + await _descendant_unit_ids(session, root)
    return False, root, ids

def _camp_order(sort_key: str):
    if sort_key == "name":   return Campaign.name.asc()
    if sort_key == "unit":   return Unit.name.asc()
    if sort_key == "status": return Campaign.active.desc(), Campaign.name.asc()
    # پیش‌فرض جدیدترین
    return Campaign.id.desc()

async def _fetch_campaigns_page(
    session, *, admin_id: int, page: int, q: Optional[str],
    sort_key: str, filters: dict, scope_root: Optional[int], allowed_units: Optional[list[int]]
):
    """
    filters = {
      "only_created_by_me": bool,
      "only_owner_root": bool,    # فقط کمپین‌های واحدِ ریشهٔ خودم (برای سوپر معنی ندارد)
      "status": "all|active|inactive",
      "origin": "all|roots|copies",   # اگر فیلد root_campaign_id دارید
      "plats": list[str],             # ["telegram","instagram",...]
    }
    """
    base = select(Campaign, Unit).join(Unit, Unit.id == Campaign.unit_id_owner)

    # محدودهٔ دید
    if allowed_units is not None and allowed_units != []:
        base = base.where(Campaign.unit_id_owner.in_(allowed_units))

    # فقط کمپین‌های واحدِ ریشهٔ خودم؟
    if not await is_superadmin(session, admin_id) and filters.get("only_owner_root"):
        if scope_root:
            base = base.where(Campaign.unit_id_owner == scope_root)

    # سازندهٔ من
    if filters.get("only_created_by_me"):
        # اگر فیلد created_by دارید استفاده می‌شود، در غیراین صورت admin_id خود کمپین (سازنده/مالک) را چک می‌کنیم
        try:
            col = getattr(Campaign, "created_by")
            base = base.where(col == admin_id)
        except Exception:
            base = base.where(Campaign.admin_id == admin_id)

    # وضعیت
    st = filters.get("status", "all")
    if st == "active":   base = base.where(Campaign.active.is_(True))
    if st == "inactive": base = base.where(Campaign.active.is_(False))

    # منشأ (اگر ستون دارید)
    try:
        origin = filters.get("origin", "all")
        col = getattr(Campaign, "root_campaign_id")
        if origin == "roots":
            base = base.where(or_(col.is_(None), col == Campaign.id))
        elif origin == "copies":
            base = base.where(col.is_not(None))
    except Exception:
        pass

    # پلتفرم‌ها (جستجو داخل JSON-Text)
    plats = filters.get("plats") or []
    if plats:
        conds = [Campaign.platforms.like(f'%"{p}"%') for p in plats]
        base = base.where(or_(*conds))

    # جستجو: نام/شهر/هشتگ
    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                func.lower(Campaign.name).like(func.lower(like)),
                func.lower(Campaign.city).like(func.lower(like)),
                func.lower(Campaign.hashtag).like(func.lower(like)),
            )
        )

    # شمارش کل
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (await session.execute(
        base.order_by(*(_camp_order(sort_key) if isinstance(_camp_order(sort_key), tuple) else (_camp_order(sort_key),)))
            .limit(CAMP_PAGE_SIZE)
            .offset(page * CAMP_PAGE_SIZE)
    )).all()  # [(Campaign, Unit), ...]

    return rows, total

def _badge(val: bool, yes="🟢", no="🔴"): return yes if val else no

def _filters_summary(f: dict) -> str:
    bits = []
    if f.get("only_created_by_me"): bits.append("سازنده=من")
    if f.get("only_owner_root"):    bits.append("مالکیت=ریشهٔ من")
    st = f.get("status","all")
    if st!="all": bits.append("وضعیت=" + ("فعال" if st=="active" else "غیرفعال"))
    org = f.get("origin","all")
    if org!="all": bits.append("منشأ=" + ("اصل" if org=="roots" else "کپی"))
    if f.get("plats"): bits.append("پلتفرم=" + ",".join(f["plats"]))
    return " | فیلتر: " + ("، ".join(bits) if bits else "—")

async def _render_campaigns_list(target_message, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
    admin_id = target_message.chat_id if getattr(target_message, "chat_id", None) else target_message.from_user.id
    st = context.user_data.setdefault("cl", {})
    page     = int(st.get("page", 0))
    q        = st.get("q")
    sort_key = st.get("sort", "new")
    filters  = st.get("filters", {"only_created_by_me": False, "only_owner_root": False, "status": "all", "origin": "all", "plats": []})

    async with SessionLocal() as s:
        is_super, root_unit, allowed_units = await _scope_for_admin(s, admin_id)
        rows, total = await _fetch_campaigns_page(
            s, admin_id=admin_id, page=page, q=q, sort_key=sort_key,
            filters=filters, scope_root=root_unit, allowed_units=allowed_units
        )

    # هدر
    head = f"📋 لیست کمپین‌ها — نتایج: {total} | مرتب‌سازی: {sort_key}" + _filters_summary(filters)
    if q: head += f' | جستجو: "{q}"'
    if total > 0:
        maxp = max(1, (total + CAMP_PAGE_SIZE - 1) // CAMP_PAGE_SIZE)
        head += f" | صفحه {page+1}/{maxp}"
    lines = [head, ""]

    # آیتم‌ها
    kb_rows: list[list[InlineKeyboardButton]] = []
    for (c, u) in rows:
        plats = _fmt_platforms(c)
        # root/copy badge (اگر ستون وجود دارد)
        try:
            is_copy = bool(getattr(c, "root_campaign_id"))
        except Exception:
            is_copy = False
        origin = "↘︎#" + str(getattr(c, "root_campaign_id")) if is_copy else "اصل"

        created_by = getattr(c, "created_by", None)
        creator_txt = f"✍️{created_by}" if created_by else f"✍️{c.admin_id}"

        item = (
            f"📣 #{c.id} | {c.name} { _badge(c.active) }\n"
            f"واحد مالک: {UNIT_TYPE_LABELS.get(u.type,u.type)} {u.name} (#{u.id})\n"
            f"👑{c.admin_id} | {creator_txt} | {origin}\n"
            f"پلتفرم‌ها: {plats} | شهر: {c.city or '—'} | هشتگ: {c.hashtag or '—'}"
        )
        lines.append(item); lines.append("")  # فاصله بین آیتم‌ها
        kb_rows.append([InlineKeyboardButton(f"مدیریت #{c.id}", callback_data=f"camp:{c.id}:manage")])

    # ناوبری و ابزار
    max_page = max(0, (total - 1)//CAMP_PAGE_SIZE)
    nav = []
    if page>0: nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"cl:page:{page-1}"))
    if page<max_page: nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"cl:page:{page+1}"))
    if nav: kb_rows.append(nav)

    tools1 = [
        InlineKeyboardButton("مرتب‌سازی", callback_data=f"cl:sort:{sort_key}"),
        InlineKeyboardButton("🔍 جستجو", callback_data="cl:search"),
        InlineKeyboardButton("🧹 پاک‌کردن", callback_data="cl:clear"),
    ]
    kb_rows.append(tools1)

    # فیلترهای سریع
    tools2 = [
        InlineKeyboardButton(("✔ " if filters.get("only_created_by_me") else "")+"سازنده=من", callback_data="cl:mine:created"),
        InlineKeyboardButton(("✔ " if filters.get("only_owner_root") else "")+"مالکیت=ریشهٔ من", callback_data="cl:mine:owner"),
    ]
    st_now = filters.get("status","all")
    tools3 = [
        InlineKeyboardButton(("[" if st_now=="all" else "")+"همه"+("]" if st_now=="all" else ""), callback_data="cl:status:all"),
        InlineKeyboardButton(("[" if st_now=="active" else "")+"فعال"+("]" if st_now=="active" else ""), callback_data="cl:status:active"),
        InlineKeyboardButton(("[" if st_now=="inactive" else "")+"غیرفعال"+("]" if st_now=="inactive" else ""), callback_data="cl:status:inactive"),
    ]
    kb_rows.append(tools2); kb_rows.append(tools3)

    # پلتفرم‌ها (تاگل چندتایی)
    plat_row = []
    cur_plats = set(filters.get("plats") or [])
    for k in PLATFORM_KEYS:
        label = ("✔ " if k in cur_plats else "") + PLATFORM_LABEL.get(k, k)
        plat_row.append(InlineKeyboardButton(label, callback_data=f"cl:plat:{k}"))
    plat_row.append(InlineKeyboardButton("اعمال پلتفرم‌ها", callback_data="cl:plat:apply"))
    kb_rows.append(plat_row)

    kb = InlineKeyboardMarkup(kb_rows)
    text = "\n".join(lines).strip() or "کمپینی پیدا نشد."
    if edit: await target_message.edit_text(text, reply_markup=kb)
    else:    await target_message.reply_text(text, reply_markup=kb)


async def campaigns_search_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("cl_wait_q"): 
        return  # به پیام‌های دیگر کاری نداشته باش
    txt = (update.message.text or "").strip()
    st = context.user_data.setdefault("cl", {})
    st["q"] = txt if txt else None
    st["page"] = 0
    context.user_data.pop("cl_wait_q", None)
    await _render_campaigns_list(update.message, context, edit=False)

async def campaigns_browse_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await safe_answer(q)
    data = q.data
    st = context.user_data.setdefault("cl", {})
    st.setdefault("filters", {"only_created_by_me": False, "only_owner_root": False, "status": "all", "origin": "all", "plats": []})

    if data.startswith("cl:page:"):
        st["page"] = int(data.split(":")[2])
        return await _render_campaigns_list(q.message, context, edit=True)

    if data.startswith("cl:sort:"):
        cur = data.split(":")[2]
        order = ("new","name","unit","status")
        try:
            i = order.index(cur)
            st["sort"] = order[(i+1)%len(order)]
        except ValueError:
            st["sort"] = "new"
        st["page"] = 0
        return await _render_campaigns_list(q.message, context, edit=True)

    if data == "cl:search":
        context.user_data["cl_wait_q"] = True
        return await q.edit_message_text("عبارت جستجو را بفرستید (نام/شهر/هشتگ):")

    if data == "cl:clear":
        st["q"] = None; st["page"]=0
        st["filters"] = {"only_created_by_me": False, "only_owner_root": False, "status": "all", "origin": "all", "plats": []}
        return await _render_campaigns_list(q.message, context, edit=True)

    if data == "cl:mine:created":
        st["filters"]["only_created_by_me"] = not st["filters"].get("only_created_by_me", False)
        st["page"]=0
        return await _render_campaigns_list(q.message, context, edit=True)

    if data == "cl:mine:owner":
        st["filters"]["only_owner_root"] = not st["filters"].get("only_owner_root", False)
        st["page"]=0
        return await _render_campaigns_list(q.message, context, edit=True)

    if data.startswith("cl:status:"):
        st["filters"]["status"] = data.split(":")[2]
        st["page"]=0
        return await _render_campaigns_list(q.message, context, edit=True)

    if data.startswith("cl:plat:"):
        key = data.split(":")[2]
        if key == "apply":
            st["page"]=0
            return await _render_campaigns_list(q.message, context, edit=True)
        cur = set(st["filters"].get("plats") or [])
        if key in cur: cur.remove(key)
        else:          cur.add(key)
        st["filters"]["plats"] = list(cur)
        # همون صفحه دوباره رفرش می‌شود تا تیک‌ها دیده شوند
        return await _render_campaigns_list(q.message, context, edit=True)

    # پیش‌فرض: بدون تغییر
    return

def manage_keyboard(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش نام", callback_data=f"edit:{campaign_id}:name")],
        [InlineKeyboardButton("#️⃣ ویرایش هشتگ", callback_data=f"edit:{campaign_id}:hashtag")],
        [InlineKeyboardButton("🏙️ ویرایش شهر", callback_data=f"edit:{campaign_id}:city")],
        [InlineKeyboardButton("🧩 ویرایش پلتفرم‌ها", callback_data=f"edit:{campaign_id}:platforms")],
        [InlineKeyboardButton("🔁 فعال/غیرفعال", callback_data=f"toggle:{campaign_id}")],
        [InlineKeyboardButton("📊 آمار", callback_data=f"stats:{campaign_id}")],
        [InlineKeyboardButton("🗂️ خروجی ZIP", callback_data=f"export:{campaign_id}")],
        [InlineKeyboardButton("🗑️ حذف", callback_data=f"delete:{campaign_id}")],
    ])

async def campaigns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    async with SessionLocal() as s:
        if not (await is_admin(s, admin_id) or await is_superadmin(s, admin_id)):
            return
    # state اولیه برای لیست
    context.user_data["cl"] = {
        "page": 0,
        "q": None,
        "sort": "new",
        "filters": {"only_created_by_me": False, "only_owner_root": False, "status": "all", "origin": "all", "plats": []}
    }
    target = update.effective_message
    await _render_campaigns_list(target, context, edit=False)

async def manage_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await safe_answer(q)
    data = q.data; admin_id = q.from_user.id

    async with SessionLocal() as s:
        if not await is_admin(s, admin_id):
            return await safe_answer(q, "اجازه ندارید.", show_alert=True)

        async def ensure_manageable_campaign(cid: int) -> Campaign | None:
            c = await get_campaign(s, cid)
            if not c: return None
            owner = c.admin_id
            if await share_scope(s, admin_id, owner):
                return c
            return None

        if data.startswith("camp:") and data.endswith(":manage"):
            cid = int(data.split(":")[1])
            camp = await ensure_manageable_campaign(cid)
            if not camp:
                return await q.edit_message_text("پیدا نشد یا متعلق به شما نیست.")
            plats = ", ".join(platforms_from_json(camp.platforms)) or "-"
            text = (
                f"#{camp.id} | {camp.name} {'🟢' if camp.active else '🔴'}\n"
                f"هشتگ: {camp.hashtag or '-'}\n"
                f"شهر: {camp.city or '-'}\n"
                f"پلتفرم‌ها: {plats}\n"
                f"توضیحات: {camp.description or '-'}"
            )
            return await q.edit_message_text(text, reply_markup=manage_keyboard(cid))

        if data.startswith("edit:"):
            _, cid_str, field = data.split(":")
            cid = int(cid_str)
            camp = await ensure_manageable_campaign(cid)
            if not camp:
                return await q.edit_message_text("پیدا نشد یا متعلق به شما نیست.")
            context.user_data["edit_field"] = (cid, field)

            if field == "platforms":
                cur = platforms_from_json(camp.platforms)  # list[str]
                context.user_data["edit_platforms"] = {"cid": cid, "picked": cur}
                return await q.edit_message_text(
                    "پلتفرم‌ها را ویرایش کنید و در پایان «💾 ثبت» را بزنید:",
                    reply_markup=platforms_keyboard(cur, prefix="epf", done_label="💾 ثبت")
                )


            # بقیهٔ فیلدها مثل قبل:
            context.user_data["await_edit_text"] = True
            return await q.edit_message_text("متن جدید را بفرستید:")

        if data.startswith("toggle:"):
            cid = int(data.split(":")[1])
            camp = await ensure_manageable_campaign(cid)
            if not camp:
                return await safe_answer(q, "اجازه ندارید.", show_alert=True)
            camp.active = not camp.active
            await s.commit()
            await q.answer("انجام شد")
            return await campaigns_cmd(update, context)

        if data.startswith("delete:"):
            cid = int(data.split(":")[1])
            camp = await ensure_manageable_campaign(cid)
            if not camp:
                return await safe_answer(q, "اجازه ندارید.", show_alert=True)
            await q.edit_message_reply_markup(InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ انصراف", callback_data=f"camp:{cid}:manage")],
                [InlineKeyboardButton("🗑️ تأیید حذف", callback_data=f"delok:{cid}")],
            ]))
            return

        if data.startswith("delok:"):
            cid = int(data.split(":")[1])
            camp = await ensure_manageable_campaign(cid)
            if not camp:
                return await safe_answer(q, "اجازه ندارید.", show_alert=True)
            await delete_campaign(s, cid); await s.commit()
            await q.answer("حذف شد")
            return await campaigns_cmd(update, context)

        if data.startswith("stats:"):
            cid = int(data.split(":")[1])  # دریافت campaign_id از callback_data
            camp = await ensure_manageable_campaign(cid)  # اطمینان از این که کمپین قابل مدیریت است
            if not camp:
                return await safe_answer(q, "اجازه ندارید.", show_alert=True)

            # استخراج آمار
            from crud import stats_by_unit_platform
            rows = await stats_by_unit_platform(s, cid)  # دریافت آمار کمپین
            if not rows:
                return await q.edit_message_text("📊 برای این کمپین هنوز گزارشی ثبت نشده.", reply_markup=manage_keyboard(cid))

            # گروه‌بندی بر اساس واحد
            by_unit = {}
            for r in rows:
                key = (r["unit_id"], r["unit_name"], r["unit_type"])
                by_unit.setdefault(key, []).append((r["platform"], r["count"]))

            # ارسال آمار با استفاده از تابع جدید
            await send_campaign_stats(q, cid, by_unit)

        if data.startswith("export:"):
            cid = int(data.split(":")[1])
            camp = await ensure_manageable_campaign(cid)
            if not camp:
                return await safe_answer(q, "اجازه ندارید.", show_alert=True)
            zippath = await export_zip(cid)
            if not zippath:
                return await safe_answer(q, "چیزی برای خروجی نیست", show_alert=True)
            await q.message.reply_document(InputFile(open(zippath, 'rb'), filename=os.path.basename(zippath)))
            return

async def edit_platforms_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)
    payload = q.data.split(":", 1)[1]  # بعد از "epf:"
    st = context.user_data.get("edit_platforms")
    if not st:
        return await q.answer("جلسهٔ ویرایش یافت نشد. دوباره «🧩 ویرایش پلتفرم‌ها» را بزنید.", show_alert=True)

    picked: list[str] = st.get("picked", [])
    cid: int = st.get("cid")

    if payload == "done":
        admin_id = q.from_user.id
        async with SessionLocal() as s:
            camp = await get_campaign(s, cid)
            if not camp or not await share_scope(s, admin_id, camp.admin_id):
                return await q.answer("اجازه ندارید.", show_alert=True)

            # ✅ اینجا JSON می‌کنیم تا خطای «type list is not supported» رفع شود
            camp.platforms = json.dumps(picked, ensure_ascii=False)
            s.add(camp)
            try:
                await s.commit()
            except Exception as e:
                await s.rollback()
                return await q.answer(f"ذخیره نشد: {e}", show_alert=True)

            # کارت مدیریت به‌روز
            plats = ", ".join(platforms_from_json(camp.platforms)) or "-"
            text = (
                f"#{camp.id} | {camp.name} {'🟢' if camp.active else '🔴'}\n"
                f"هشتگ: {camp.hashtag or '-'}\n"
                f"شهر: {camp.city or '-'}\n"
                f"پلتفرم‌ها: {plats}\n"
                f"توضیحات: {camp.description or '-'}"
            )

        # پاکسازی حالت ویرایش
        context.user_data.pop("edit_platforms", None)
        context.user_data.pop("edit_field", None)
        return await q.edit_message_text(text, reply_markup=manage_keyboard(cid))

    # تاگل آیتم‌ها
    if payload in PLATFORM_KEYS:
        if payload in picked:
            picked.remove(payload)
        else:
            picked.append(payload)
        st["picked"] = picked
        context.user_data["edit_platforms"] = st
        return await q.edit_message_reply_markup(
            reply_markup=platforms_keyboard(picked, prefix="epf", done_label="💾 ثبت")
        )

    return await q.answer("گزینه نامعتبر.", show_alert=True)

async def edit_text_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("edit_field"): return
    cid, field = context.user_data.get("edit_field")
    text = (update.message.text or "").strip()
    admin_id = update.effective_user.id
    async with SessionLocal() as s:
        if not await is_admin(s, admin_id): return await update.message.reply_text("اجازه ندارید.")
        c = await get_campaign(s, cid)
        if not c or not await share_scope(s, admin_id, c.admin_id):
            return await update.message.reply_text("اجازه ندارید.")
        if field == "platforms":
            # انتظار JSON لیست
            try:
                arr = json.loads(text); assert isinstance(arr, list)
                c.platforms = json.dumps(arr, ensure_ascii=False)
            except Exception:
                return await update.message.reply_text("فرمت JSON معتبر نیست. مثال: [\"telegram\",\"instagram\"]")
        elif field in ("name","hashtag","city","description"):
            await update_campaign_field(s, cid, field, text)
        else:
            return await update.message.reply_text("فیلد نامعتبر.")
        await s.commit()
    context.user_data.pop("edit_field", None)
    await update.message.reply_text("ثبت شد ✅")

def _fa_platform_dir(platform: str) -> str:
    if not platform or platform == "unknown":
        return "نامشخص"
    label = PLATFORM_LABEL.get(platform, platform)
    # حذف پیشوندهای ایموجی/کاراکترهای تزئینی که در لیبل‌های UI هست
    for junk in ("◽️ ", "✅ ", "▫️ ", "• "):
        if label.startswith(junk):
            label = label[len(junk):]
    # جلوگیری از شکستن مسیر داخل زیپ
    label = label.replace("/", "／").strip()
    return label or "نامشخص"


async def export_zip(campaign_id: int) -> Optional[str]:
    bases = [p for p in DATA_DIR.glob(f"*/c{campaign_id}") if p.is_dir()]
    if not bases:
        return None

    files = []
    for base in bases:
        for file in base.rglob("*"):
            if not file.is_file():
                continue
            rel = file.relative_to(base)
            parts = rel.parts  # [platform, u{unit_id}, filename, ...]
            if len(parts) >= 3 and parts[1].startswith("u"):
                platform = parts[0]
                uid = parts[1][1:]  # حذف 'u'
                files.append((str(file), platform, uid))
            else:
                files.append((str(file), "unknown", "unknown"))

    if not files:
        return None

    zip_name = DATA_DIR / f"c{campaign_id}_export.zip"
    if zip_name.exists():
        zip_name.unlink()

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, platform, uid in files:
            filename = os.path.basename(file_path)
            plat_dir = _fa_platform_dir(platform)  # پوشهٔ فارسیِ پلتفرم
            arcname = f"{plat_dir}/{uid}__{filename}"
            zf.write(file_path, arcname=arcname)

    return str(zip_name)
