# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database import SessionLocal
from models import Admin, City, User
from crud import (
    is_admin, is_superadmin, set_user_admin, set_user_name, list_my_users, del_user,
    visible_cluster_ids
)

def _extract_user_id_and_name(update: Update) -> tuple[int|None, str|None]:
    msg = update.effective_message
    parts = (msg.text or "").split(maxsplit=2)  # /adduser <id> <name...>
    uid = None; name = None
    if len(parts) >= 2 and parts[1].isdigit():
        uid = int(parts[1]); name = parts[2].strip().strip('"').strip("'") if len(parts) >= 3 else None
    elif msg.reply_to_message and msg.reply_to_message.from_user:
        uid = msg.reply_to_message.from_user.id
        if len(parts) >= 2: name = parts[1].strip().strip('"').strip("'")
    return uid, name

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        caller = update.effective_user.id
        if not await is_superadmin(s, caller):
            return await update.effective_message.reply_text("⛔️ فقط سوپرادمین می‌تواند ادمین جدید اضافه کند.")
        parts = (update.effective_message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            return await update.effective_message.reply_text("فرمت: /addadmin <user_id> [role]\nمثال: /addadmin 5018729099 L1")
        uid = int(parts[1]); role = parts[2] if len(parts) >= 3 else "L1"
        adm = await s.get(Admin, uid)
        if not adm:
            s.add(Admin(admin_id=uid, role=role))
        else:
            adm.role = role
        await s.commit()
    await update.effective_message.reply_text(f"✅ admin {uid} با نقش «{role}» ثبت/به‌روزرسانی شد.")

async def linkadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        caller = update.effective_user.id
        text = (update.effective_message.text or "")
        nums = re.findall(r"\d+", text)
        if len(nums) == 1:
            parent_id = caller; child_id = int(nums[0])
        elif len(nums) == 2:
            if not await is_superadmin(s, caller):
                return await update.effective_message.reply_text("⛔️ فقط سوپرادمین می‌تواند والد دیگری تعیین کند.")
            parent_id = int(nums[0]); child_id = int(nums[1])
        else:
            return await update.effective_message.reply_text(
                "فرمت درست:\n"
                "• <code>/linkadmin &lt;child_id&gt;</code>\n"
                "• (SUPER) <code>/linkadmin &lt;parent_id&gt; &lt;child_id&gt;</code>",
                parse_mode=ParseMode.HTML
            )
        # ensure admins exist
        if not await s.get(Admin, parent_id):
            s.add(Admin(admin_id=parent_id, role="L1"))
        if not await s.get(Admin, child_id):
            s.add(Admin(admin_id=child_id, role="L1"))
        AdminTree = __import__("models").AdminTree
        if not await s.get(AdminTree, {"parent_admin_id": parent_id, "child_admin_id": child_id}):
            s.add(AdminTree(parent_admin_id=parent_id, child_admin_id=child_id))
        await s.commit()
    await update.effective_message.reply_text(f"✅ لینک والد-فرزند ثبت شد: {parent_id} → {child_id}")

async def myusers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        admin_id = update.effective_user.id
        if not await is_admin(s, admin_id):
            return
        rows = await list_my_users(s, admin_id)
        if not rows:
            return await update.effective_message.reply_text("هنوز کاربری اضافه نکرده‌اید.")
        lines = ["👥 لیست کاربرهای شما:"]
        for r in rows:
            cname = "—"
            if r.city_id:
                city = await s.get(City, r.city_id)
                cname = city.name if city else "—"
            dn = (r.display_name or "").strip()
            lines.append(f"- {r.user_id}" + (f" | {dn}" if dn else "") + f" | 🏙️ {cname}")
    await update.effective_message.reply_text("\n".join(lines))

async def adduser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        admin_id = update.effective_user.id
        if not await is_admin(s, admin_id):
            return await update.effective_message.reply_text("⛔️ شما ادمین نیستید.")
        uid, name = _extract_user_id_and_name(update)
        if not uid:
            return await update.effective_message.reply_text(
                "کاربر را مشخص کنید:\n"
                "۱) `/adduser 5018729099 علی رضایی`\n"
                "۲) ریپلای به پیام کاربر: `/adduser علی رضایی`\n"
                "۳) فقط با آی‌دی: `/adduser 5018729099`", parse_mode="Markdown"
            )
        await set_user_admin(s, uid, admin_id, name); await s.commit()
    shown = f"{uid}" + (f" ({name})" if name else "")
    await update.effective_message.reply_text(f"✅ کاربر {shown} به زیرمجموعه شما اضافه شد.")

async def renameuser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        admin_id = update.effective_user.id
        if not await is_admin(s, admin_id):
            return await update.effective_message.reply_text("⛔️ شما ادمین نیستید.")
        parts = (update.effective_message.text or "").split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            return await update.effective_message.reply_text("فرمت درست: `/renameuser 5018729099 نام جدید`", parse_mode="Markdown")
        uid = int(parts[1]); name = parts[2].strip()
        # check cluster
        owner = await s.get(User, uid)
        if not owner or owner.admin_id not in set(await visible_cluster_ids(s, admin_id)):
            return await update.effective_message.reply_text("⛔️ این کاربر در حوزهٔ شما نیست.")
        await set_user_name(s, uid, name); await s.commit()
    await update.effective_message.reply_text(f"✅ نام کاربر {uid} به «{name}» تغییر کرد.")

async def deluser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        admin_id = update.effective_user.id
        if not await is_admin(s, admin_id):
            return await update.effective_message.reply_text("⛔️ شما ادمین نیستید.")
        msg = update.effective_message
        parts = (msg.text or "").split()
        uid = None
        if len(parts) >= 2 and parts[1].isdigit():
            uid = int(parts[1])
        elif msg.reply_to_message and msg.reply_to_message.from_user:
            uid = msg.reply_to_message.from_user.id
        if not uid:
            return await update.effective_message.reply_text(
                "کاربر را مشخص کنید:\n• `/deluser 5018729099`\n• یا ریپلای کنید: `/deluser`",
                parse_mode="Markdown"
            )
        owner = await s.get(User, uid)
        if not owner or owner.admin_id not in set(await visible_cluster_ids(s, admin_id)):
            return await update.effective_message.reply_text("⛔️ این کاربر در حوزهٔ شما نیست.")
        ok = await del_user(s, uid, owner.admin_id); await s.commit()
    await update.effective_message.reply_text("✅ حذف شد." if ok else "یافت نشد.")
