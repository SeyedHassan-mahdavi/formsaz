# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from database import SessionLocal
from models import Admin, Unit, UnitAdmin, AdminTree, User, Campaign
from keyboards import UNIT_TYPE_LABELS
from crud import (
    is_admin, is_superadmin, get_primary_unit_for_admin,
    get_admin_units, list_campaigns_for_admin_units,
)

# ----- helpers -----
async def _unit_path(session, unit_id: int) -> str:
    """label path like: کشور X ⟵ استان Y ⟵ شهر Z ⟵ ..."""
    if not unit_id:
        return "—"
    chain = []
    cur = await session.get(Unit, unit_id)
    while cur:
        chain.append(f"{UNIT_TYPE_LABELS.get(cur.type, cur.type)} {cur.name}")
        if not cur.parent_id:
            break
        cur = await session.get(Unit, cur.parent_id)
    return " ⟵ ".join(reversed(chain))

async def _units_with_roles(session, admin_id: int) -> List[Tuple[Unit, str]]:
    """All units this admin is attached to with their role on each (OWNER/ASSISTANT)."""
    q = await session.execute(
        select(Unit, UnitAdmin.role).join(UnitAdmin, Unit.id == UnitAdmin.unit_id)
        .where(UnitAdmin.admin_id == admin_id).order_by(Unit.type, Unit.name)
    )
    return [(u, role) for u, role in q.all()]

async def _assistants_for_owner(session, admin_id: int) -> List[Tuple[int, Unit]]:
    """
    Assistants assigned on any unit where THIS admin is OWNER.
    Returns list of (assistant_admin_id, unit)
    """
    owner_unit_ids = (
        await session.execute(
            select(UnitAdmin.unit_id).where(UnitAdmin.admin_id == admin_id, UnitAdmin.role == "OWNER")
        )
    ).scalars().all()
    if not owner_unit_ids:
        return []
    q = await session.execute(
        select(UnitAdmin.admin_id, Unit)
        .join(Unit, Unit.id == UnitAdmin.unit_id)
        .where(UnitAdmin.unit_id.in_(owner_unit_ids), UnitAdmin.role == "ASSISTANT")
        .order_by(Unit.name)
    )
    return [(aid, u) for aid, u in q.all()]

async def _direct_users_count(session, admin_id: int) -> int:
    """Users directly owned by this admin (not the whole cluster)."""
    return (
        await session.execute(select(func.count()).select_from(User).where(User.admin_id == admin_id))
    ).scalar_one()

async def _children_admins_count(session, admin_id: int) -> int:
    """Direct sub-admins in AdminTree (child count)."""
    return (
        await session.execute(select(func.count()).select_from(AdminTree).where(AdminTree.parent_admin_id == admin_id))
    ).scalar_one()

def _profile_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 واحدهای من", callback_data=f"{prefix}:units")],
        [InlineKeyboardButton("🤝 دستیارها", callback_data=f"{prefix}:assistants")],
        [InlineKeyboardButton("📣 کمپین‌های واحدهای من", callback_data=f"{prefix}:camps")],
        [InlineKeyboardButton("◀️ بازگشت", callback_data=f"{prefix.split(':')[0]}:home")],  # sa:home یا adm:home
    ])

# ----- overview text -----
async def _build_profile_text(session, admin_id: int) -> str:
    admin = await session.get(Admin, admin_id)
    role = getattr(admin, "role", "—") if admin else "—"

    primary_unit_id = await get_primary_unit_for_admin(session, admin_id)
    primary_path = await _unit_path(session, primary_unit_id) if primary_unit_id else "—"

    units_all = await _units_with_roles(session, admin_id)
    units_count = len(units_all)

    assistants = await _assistants_for_owner(session, admin_id)
    assistants_count = len({aid for aid, _ in assistants})

    direct_users = await _direct_users_count(session, admin_id)
    children_admins = await _children_admins_count(session, admin_id)

    camps_all = await list_campaigns_for_admin_units(session, admin_id, active_only=False)
    active_count = len([c for c in camps_all if c.active])
    total_count = len(camps_all)

    lines = [
        "👤 پروفایل ادمین",
        f"🆔 Admin ID: {admin_id}",
        f"🎖 نقش: {role}",
        f"🏛 واحد اصلی: {primary_path}",
        f"🏢 تعداد واحدهای متصل: {units_count}",
        f"🤝 تعداد دستیارهای واحدی: {assistants_count}",
        f"🧭 تعداد زیرادمین‌ها (درخت): {children_admins}",
        f"👥 کاربران مستقیم: {direct_users}",
        f"📣 کمپین‌ها (فعال/کل): {active_count}/{total_count}",
    ]
    return "\n".join(lines)

# ----- handlers -----
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /profile """
    uid = update.effective_user.id
    async with SessionLocal() as s:
        if not await is_admin(s, uid):
            return await update.effective_message.reply_text("فقط ادمین‌ها پروفایل دارند.")
        text = await _build_profile_text(s, uid)
        prefix = "sa:profile" if await is_superadmin(s, uid) else "adm:profile"
    await update.effective_message.reply_text(text, reply_markup=_profile_kb(prefix))

async def profile_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ sa:profile | adm:profile | sa:profile:units | ... """
    q = update.callback_query
    parts = (q.data or "").split(":")
    role = parts[0]   # sa | adm
    action = parts[2] if len(parts) >= 3 else None

    uid = q.from_user.id
    async with SessionLocal() as s:
        if not await is_admin(s, uid):
            return await q.edit_message_text("اجازه ندارید.")
        prefix = f"{role}:profile"

        # overview
        if action is None or action == "":
            text = await _build_profile_text(s, uid)
            return await q.edit_message_text(text, reply_markup=_profile_kb(prefix))

        if action == "units":
            rows = await _units_with_roles(s, uid)
            if not rows:
                return await q.edit_message_text("به هیچ واحدی متصل نیستید.", reply_markup=_profile_kb(prefix))
            lines = ["🏢 واحدهای متصل:"]
            for u, r in rows:
                lines.append(f"• {UNIT_TYPE_LABELS.get(u.type,u.type)} {u.name} (#{u.id}) — {r}")
            return await q.edit_message_text("\n".join(lines), reply_markup=_profile_kb(prefix))

        if action == "assistants":
            pairs = await _assistants_for_owner(s, uid)
            if not pairs:
                return await q.edit_message_text("برای واحدهای مالک شما دستیار تعریف نشده.", reply_markup=_profile_kb(prefix))
            # گروه‌بندی بر اساس واحد
            m = {}
            for aid, u in pairs:
                m.setdefault(u, set()).add(aid)
            lines = ["🤝 دستیارها (بر اساس واحد مالک):"]
            for u, aids in m.items():
                lines.append(f"\n{UNIT_TYPE_LABELS.get(u.type,u.type)} {u.name} (#{u.id})")
                for aid in sorted(aids):
                    lines.append(f"  • admin_id={aid}")
            return await q.edit_message_text("\n".join(lines), reply_markup=_profile_kb(prefix))

        if action == "camps":
            camps = await list_campaigns_for_admin_units(s, uid, active_only=False)
            if not camps:
                return await q.edit_message_text("کمپینی برای واحدهای شما ثبت نشده.", reply_markup=_profile_kb(prefix))
            lines = ["📣 کمپین‌های واحدهای من:"]
            for c in camps[:50]:  # لیست خیلی بزرگ نشود
                lines.append(f"• #{c.id} | {c.name} {'🟢' if c.active else '🔴'}")
            if len(camps) > 50:
                lines.append(f"... و {len(camps)-50} مورد دیگر")
            return await q.edit_message_text("\n".join(lines), reply_markup=_profile_kb(prefix))

        # default
        text = await _build_profile_text(s, uid)
        return await q.edit_message_text(text, reply_markup=_profile_kb(prefix))
