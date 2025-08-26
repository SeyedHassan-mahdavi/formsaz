# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from typing import Optional, List, Tuple
from sqlalchemy import select, func, update, delete, literal, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from models import (
    Campaign, Report, ReportItem, ReportItemRef, User, City, Admin, AdminTree, Unit, UnitAdmin, CampaignCopy
)
from datetime import datetime, timezone
from keyboards import PLATFORM_KEYS

def platforms_to_json(keys: List[str]) -> str:
    return json.dumps([k for k in keys if k in PLATFORM_KEYS], ensure_ascii=False)

def platforms_from_json(text: str) -> List[str]:
    try:
        arr = json.loads(text)
        return [k for k in arr if k in PLATFORM_KEYS]
    except Exception:
        return []

# --- Admin / Roles ---

async def is_admin(session: AsyncSession, user_id: int) -> bool:
    q = await session.execute(select(Admin).where(Admin.admin_id == user_id))
    return q.scalar_one_or_none() is not None

async def is_superadmin(session: AsyncSession, user_id: int) -> bool:
    q = await session.execute(select(Admin.role).where(Admin.admin_id == user_id))
    r = q.scalar_one_or_none()
    return bool(r and r == "SUPER")

async def ancestors_of(session: AsyncSession, admin_id: int) -> list[int]:
    # ساده: چندکوئری
    parents = []
    cur = admin_id
    while True:
        row = await session.execute(select(AdminTree.parent_admin_id).where(AdminTree.child_admin_id == cur))
        p = row.scalar_one_or_none()
        if not p: break
        parents.append(p); cur = p
    return parents

async def descendants_of(session: AsyncSession, admin_id: int) -> list[int]:
    children = []
    stack = [admin_id]
    while stack:
        cur = stack.pop()
        rows = await session.execute(select(AdminTree.child_admin_id).where(AdminTree.parent_admin_id == cur))
        kids = [x for (x,) in rows.all()]
        children.extend(kids)
        stack.extend(kids)
    return [x for x in children if x != admin_id]

async def admin_scope_ids(session: AsyncSession, admin_id: int) -> list[int]:
    # self + descendants
    return [admin_id] + await descendants_of(session, admin_id)

async def visible_cluster_ids(session: AsyncSession, admin_id: int) -> list[int]:
    return list(dict.fromkeys((await ancestors_of(session, admin_id)) + [admin_id] + (await descendants_of(session, admin_id))))

async def can_manage_admin(session: AsyncSession, manager_id: int, target_admin_id: int) -> bool:
    if manager_id == target_admin_id:
        return True
    if await is_superadmin(session, manager_id):
        return True
    return target_admin_id in set(await admin_scope_ids(session, manager_id))

async def share_scope(session: AsyncSession, a: int, b: int) -> bool:
    return await can_manage_admin(session, a, b) or await can_manage_admin(session, b, a) or a == b or await is_superadmin(session, a)

async def primary_owner_id(session: AsyncSession, admin_id: int) -> int:
    chain = [admin_id] + (await ancestors_of(session, admin_id))
    st = await session.execute(select(Admin.admin_id, Admin.role).where(Admin.admin_id.in_(chain)))
    roles = {aid: role for aid, role in st.all()}
    for aid in chain:
        if roles.get(aid) != "SUPER":
            return aid
    return admin_id

# --- Users & Cities ---

async def set_user_admin(session: AsyncSession, user_id: int, admin_id: int, display_name: str | None):
    owner = await primary_owner_id(session, admin_id)
    u = await session.get(User, user_id)
    if not u:
        u = User(user_id=user_id, admin_id=owner, display_name=display_name)
        session.add(u)
    else:
        u.admin_id = owner
        if display_name:
            u.display_name = display_name

async def get_user_admin(session: AsyncSession, user_id: int) -> Optional[int]:
    u = await session.get(User, user_id)
    return u.admin_id if u else None

async def set_user_name(session: AsyncSession, user_id: int, display_name: str):
    u = await session.get(User, user_id)
    if u:
        u.display_name = display_name

async def list_my_users(session: AsyncSession, admin_id: int) -> list[User]:
    ids = await visible_cluster_ids(session, admin_id)
    q = await session.execute(select(User).where(User.admin_id.in_(ids)).order_by(User.user_id))
    return [x for x in q.scalars().all()]

async def del_user(session: AsyncSession, user_id: int, admin_id: int) -> bool:
    q = await session.execute(select(User).where(User.user_id == user_id, User.admin_id == admin_id))
    u = q.scalar_one_or_none()
    if not u: return False
    await session.delete(u)
    return True

async def add_city(session: AsyncSession, admin_id: int, name: str) -> int:
    owner = await primary_owner_id(session, admin_id)
    c = City(name=name.strip(), admin_id=owner, created_at=func.strftime('%Y-%m-%dT%H:%M:%fZ', func.now()))
    session.add(c)
    await session.flush()
    return c.id

async def list_cities(session: AsyncSession, admin_id: int) -> list[City]:
    ids = await visible_cluster_ids(session, admin_id)
    q = await session.execute(select(City).where(City.admin_id.in_(ids)).order_by(City.name))
    return [x for x in q.scalars().all()]

async def get_city(session: AsyncSession, city_id: int) -> Optional[City]:
    return await session.get(City, city_id)

# --- Units ---

async def get_admin_units(session: AsyncSession, admin_id: int) -> list[int]:
    q = await session.execute(select(UnitAdmin.unit_id).where(UnitAdmin.admin_id == admin_id))
    return [x for (x,) in q.all()]

async def get_primary_unit_for_admin(session: AsyncSession, admin_id: int) -> Optional[int]:
    units = await get_admin_units(session, admin_id)
    return units[0] if units else None

async def get_unit(session: AsyncSession, unit_id: int) -> Optional[Unit]:
    return await session.get(Unit, unit_id)

async def child_units(session: AsyncSession, unit_id: int, child_type: str | None = None) -> list[Unit]:
    stmt = select(Unit).where(Unit.parent_id == unit_id)
    if child_type:
        stmt = stmt.where(Unit.type == child_type)
    stmt = stmt.order_by(Unit.name)
    q = await session.execute(stmt)
    return [x for x in q.scalars().all()]

async def parent_unit_id(session: AsyncSession, unit_id: int) -> Optional[int]:
    u = await get_unit(session, unit_id)
    return u.parent_id if u else None

# --- Campaigns / Reports ---

async def create_campaign_v2(session: AsyncSession, owner_unit_id: int, owner_admin_id: int,
                             name: str, platforms: list[str],
                             description: str | None, hashtag: str | None,
                             city_label: str | None, config: dict) -> int:
    import json
    from utils import now_iso
    camp = Campaign(
        name=name, hashtag=hashtag, city=city_label, platforms=platforms_to_json(platforms),
        description=description, active=True, created_by=owner_admin_id, created_at=now_iso(),
        admin_id=owner_admin_id, root_campaign_id=None, unit_id_owner=owner_unit_id,
        config_json=json.dumps(config, ensure_ascii=False), status='ACTIVE'
    )
    session.add(camp)
    await session.flush()
    camp.root_campaign_id = camp.id
    await session.flush()
    return camp.id

async def get_campaign(session: AsyncSession, cid: int) -> Optional[Campaign]:
    return await session.get(Campaign, cid)

async def list_campaigns_for_admin_units(session: AsyncSession, admin_id: int, active_only: bool=False) -> list[Campaign]:
    unit_ids = await get_admin_units(session, admin_id)
    if not unit_ids: return []
    stmt = select(Campaign).where(Campaign.unit_id_owner.in_(unit_ids))
    if active_only:
        stmt = stmt.where(Campaign.active.is_(True))
    stmt = stmt.order_by(Campaign.id.desc())
    q = await session.execute(stmt)
    return [x for x in q.scalars().all()]

async def update_campaign_field(session: AsyncSession, campaign_id: int, field: str, value):
    camp = await session.get(Campaign, campaign_id)
    if not camp: return
    setattr(camp, field, value)

async def delete_campaign(session: AsyncSession, campaign_id: int):
    camp = await session.get(Campaign, campaign_id)
    if camp:
        await session.delete(camp)

async def get_or_create_open_report(session: AsyncSession, user_id: int, campaign_id: int, platform: str, city_id: int | None) -> int:
    from utils import now_iso
    stmt = select(Report.id).where(
        Report.user_id==user_id, Report.campaign_id==campaign_id, Report.platform==platform
    )
    if city_id is None:
        stmt = stmt.where(Report.city_id.is_(None))
    else:
        stmt = stmt.where(Report.city_id==city_id)
    q = await session.execute(stmt)
    rid = q.scalar_one_or_none()
    if rid:
        return rid
    r = Report(user_id=user_id, campaign_id=campaign_id, platform=platform, created_at=now_iso(), city_id=city_id)
    session.add(r); await session.flush()
    return r.id


async def add_report_item(
    session,
    report_id: int,
    file_id: str | None,
    file_path: str,
    platform: str,
    file_name: str,                        # ⬅️ جدید
) -> int:
    item = ReportItem(
        report_id=report_id,
        file_id=file_id,
        file_path=file_path,
        file_name=file_name,               # ⬅️ حتماً مقدار بده
        platform=platform,
        created_at=datetime.utcnow().isoformat(),
    )
    session.add(item)
    await session.flush()                  # تا item.id پر بشه
    return item.id

async def stats_for_campaign(session: AsyncSession, campaign_id: int) -> list[tuple[str,int]]:
    q = await session.execute(
        select(Report.platform, func.count(ReportItem.id))
        .join(ReportItem, ReportItem.report_id==Report.id, isouter=True)
        .where(Report.campaign_id==campaign_id)
        .group_by(Report.platform)
        .order_by(Report.platform)
    )
    return [(p, c or 0) for p, c in q.all()]

async def stats_for_user_campaign(session: AsyncSession, campaign_id: int, user_id: int) -> list[tuple[str,int]]:
    q = await session.execute(
        select(Report.platform, func.count(ReportItem.id))
        .join(ReportItem, ReportItem.report_id==Report.id, isouter=True)
        .where(Report.campaign_id==campaign_id, Report.user_id==user_id)
        .group_by(Report.platform)
        .order_by(Report.platform)
    )
    return [(p, c or 0) for p, c in q.all()]


async def stats_by_unit_platform(session, campaign_id: int):
    """
    خروجی: لیستی از دیکشنری‌ها با کلیدهای: unit_id, unit_name, unit_type, platform, count
    """
    q = (
        select(
            Unit.id.label("unit_id"),
            Unit.name.label("unit_name"),
            Unit.type.label("unit_type"),
            ReportItem.platform.label("platform"),
            func.count(ReportItem.id).label("count"),
        )
        .join(Report, ReportItem.report_id == Report.id)
        .join(Unit, Report.unit_id_owner == Unit.id)
        .where(Report.campaign_id == campaign_id)
        .group_by(Unit.id, Unit.name, Unit.type, ReportItem.platform)
        .order_by(Unit.type, Unit.name)
    )
    rows = (await session.execute(q)).all()
    # نرمالایز به ساختار ساده
    return [
        {
            "unit_id": r.unit_id,
            "unit_name": r.unit_name,
            "unit_type": r.unit_type,
            "platform": r.platform,
            "count": r.count,
        }
        for r in rows
    ]


async def list_campaigns_for_admin(session: AsyncSession, admin_id: int, active_only: bool=False) -> list[Campaign]:
    ids = await visible_cluster_ids(session, admin_id)
    if not ids: return []
    stmt = select(Campaign).where(Campaign.admin_id.in_(ids))
    if active_only:
        stmt = stmt.where(Campaign.active.is_(True))
    stmt = stmt.order_by(Campaign.id.desc())
    q = await session.execute(stmt)
    return [x for x in q.scalars().all()]

async def list_campaigns_for_user(session: AsyncSession, user_id: int, active_only: bool=True) -> list[Campaign]:
    admin_id = await get_user_admin(session, user_id)
    if admin_id is None:
        return []
    return await list_campaigns_for_admin(session, admin_id, active_only=active_only)


async def get_or_create_report(session: AsyncSession, user_id: int, campaign_id: int, platform: str, city_id: int | None = None) -> int:
    from utils import now_iso
    stmt = select(Report.id).where(
        Report.user_id == user_id, 
        Report.campaign_id == campaign_id, 
        Report.platform == platform
    )
    if city_id is None:
        stmt = stmt.where(Report.city_id.is_(None))
    else:
        stmt = stmt.where(Report.city_id == city_id)
    q = await session.execute(stmt)
    report_id = q.scalar_one_or_none()
    
    if report_id:
        return report_id  # گزارش موجود را برمی‌گرداند
    # در غیر این صورت، یک گزارش جدید ایجاد می‌شود
    new_report = Report(
        user_id=user_id,
        campaign_id=campaign_id,
        platform=platform,
        created_at=now_iso(),
        city_id=city_id
    )
    session.add(new_report)
    await session.flush()  # فلش برای گرفتن شناسه گزارش جدید
    return new_report.id


# --- Campaign listing by unit tree (drop-in for crud.py) ---
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import Unit, Campaign
# اگر بالاتر همین‌ها را از قبل import داری، تکراری بودن مشکلی ندارد.

async def _descendant_unit_ids(session: AsyncSession, root_id: int) -> list[int]:
    """همهٔ آی‌دیِ زیرواحدهای یک ریشه را با BFS برمی‌گرداند (خودِ ریشه را شامل نمی‌شود)."""
    out: list[int] = []
    queue: list[int] = [root_id]
    while queue:
        cur = queue.pop(0)
        child_ids: list[int] = (
            await session.execute(select(Unit.id).where(Unit.parent_id == cur))
        ).scalars().all()
        if not child_ids:
            continue
        out.extend(child_ids)
        queue.extend(child_ids)
    return out

async def list_campaigns_for_admin_unit_tree(session: AsyncSession, admin_id: int, active_only: bool=False) -> list[Campaign]:
    """اگر سوپر باشد همهٔ کمپین‌ها؛ اگر ادمین معمولی باشد کمپین‌های واحدهای متصل + همهٔ زیرواحدها."""
    # سوپرادمین = همهٔ کمپین‌ها
    if await is_superadmin(session, admin_id):
        stmt = select(Campaign)
        if active_only:
            stmt = stmt.where(Campaign.active.is_(True))
        stmt = stmt.order_by(Campaign.id.desc())
        return (await session.execute(stmt)).scalars().all()

    # ادمین معمولی: واحدهای وصل‌شده + همهٔ زیرواحدها
    roots = await get_admin_units(session, admin_id)  # [unit_id, ...]
    if not roots:
        return []
    unit_ids: set[int] = set(roots)
    for r in roots:
        unit_ids.update(await _descendant_unit_ids(session, r))

    stmt = select(Campaign).where(Campaign.unit_id_owner.in_(unit_ids))
    if active_only:
        stmt = stmt.where(Campaign.active.is_(True))
    stmt = stmt.order_by(Campaign.id.desc())
    return (await session.execute(stmt)).scalars().all()

    # سوپرادمین = همهٔ کمپین‌ها
    if await is_superadmin(session, admin_id):
        stmt = select(Campaign)
        if active_only:
            stmt = stmt.where(Campaign.active.is_(True))
        stmt = stmt.order_by(Campaign.id.desc())
        q = await session.execute(stmt)
        return [x for x in q.scalars().all()]

    # ادمین معمولی: واحدهای وصل‌شده + همهٔ زیرواحدها
    roots = await get_admin_units(session, admin_id)  # [unit_id,...]
    if not roots:
        return []
    all_units = set(roots)
    for u in roots:
        all_units.update(await _descendant_unit_ids(session, u))

    stmt = select(Campaign).where(Campaign.unit_id_owner.in_(all_units))
    if active_only:
        stmt = stmt.where(Campaign.active.is_(True))
    stmt = stmt.order_by(Campaign.id.desc())
    q = await session.execute(stmt)
    return [x for x in q.scalars().all()]
    # سوپرادمین = همه کمپین‌ها
    if await is_superadmin(session, admin_id):
        stmt = select(Campaign)
        if active_only:
            stmt = stmt.where(Campaign.active.is_(True))
        stmt = stmt.order_by(Campaign.id.desc())
        q = await session.execute(stmt)
        return [x for x in q.scalars().all()]

    # ادمین معمولی: همه واحدهایی که به آن‌ها وصل است + تمام زیرواحدها
    root_units = await get_admin_units(session, admin_id)  # [unit_id,...]
    if not root_units:
        return []
    all_unit_ids = set(root_units)
    for uid in root_units:
        all_unit_ids.update(await _descendant_unit_ids(session, uid))

    stmt = select(Campaign).where(Campaign.unit_id_owner.in_(all_unit_ids))
    if active_only:
        stmt = stmt.where(Campaign.active.is_(True))
    stmt = stmt.order_by(Campaign.id.desc())
    q = await session.execute(stmt)
    return [x for x in q.scalars().all()]    





async def get_user_unit_id(session: AsyncSession, tg_user_id: int) -> Optional[int]:
    """
    واحدِ کاربر را برمی‌گرداند:
    - اگر User.unit_id_paygah ست شده باشد از همان استفاده می‌شود
    - در غیر این صورت اگر کاربر ادمین باشد از واحد اصلی ادمین استفاده می‌شود
    """
    u = await session.get(User, tg_user_id)
    if u and u.unit_id_paygah:
        return u.unit_id_paygah
    # fallback برای ادمین‌ها
    try:
        pu = await get_primary_unit_for_admin(session, tg_user_id)
        return pu
    except Exception:
        return None

async def list_reportable_campaigns_for_user(session: AsyncSession, tg_user_id: int, active_only: bool=True) -> list[Campaign]:
    """
    فقط کمپین‌های «واحد والد» کاربر را برمی‌گرداند تا کاربر/واحدِ زیرمجموعه روی آن‌ها گزارش بدهد.
    """
    uid = await get_user_unit_id(session, tg_user_id)
    if not uid:
        return []
    pid = await parent_unit_id(session, uid)
    if not pid:
        return []  # اگر والد ندارد، چیزی برای گزارش‌دادن نیست
    stmt = select(Campaign).where(Campaign.unit_id_owner == pid)
    if active_only:
        stmt = stmt.where(Campaign.active.is_(True))
    stmt = stmt.order_by(Campaign.id.desc())
    q = await session.execute(stmt)
    return [x for x in q.scalars().all()]




async def list_units_for_actor(session: AsyncSession, actor_id: int) -> List[Unit]:
    """سوپرادمین: همه واحدها. ادمین: فقط واحدهای متصل به خودش."""
    if await is_superadmin(session, actor_id):
        stmt = select(Unit).order_by(Unit.type, Unit.name)
        return (await session.execute(stmt)).scalars().all()
    unit_ids = await get_admin_units(session, actor_id)
    if not unit_ids:
        return []
    stmt = select(Unit).where(Unit.id.in_(unit_ids)).order_by(Unit.type, Unit.name)
    return (await session.execute(stmt)).scalars().all()

async def list_campaigns_reported_by_unit(session: AsyncSession, unit_id: int, active_only: bool=True) -> List[Campaign]:
    """فقط کمپین‌هایی که این واحد واقعاً رویشان گزارش ثبت کرده."""
    stmt = (
        select(Campaign)
        .join(Report, Report.campaign_id == Campaign.id)
        .where(Report.unit_id_owner == unit_id)
        .group_by(Campaign.id)
        .order_by(Campaign.id.desc())
    )
    if active_only:
        stmt = stmt.where(Campaign.active.is_(True))
    return (await session.execute(stmt)).scalars().all()

async def stats_for_unit_campaign(session: AsyncSession, unit_id: int, campaign_id: int) -> List[Tuple[str, int]]:
    """آمار یک واحد در یک کمپین: [(platform, count)]"""
    q = (
        select(Report.platform, func.count(ReportItem.id))
        .join(ReportItem, ReportItem.report_id == Report.id, isouter=True)
        .where(Report.campaign_id == campaign_id, Report.unit_id_owner == unit_id)
        .group_by(Report.platform)
        .order_by(Report.platform)
    )
    return [(p, c or 0) for p, c in (await session.execute(q)).all()]

async def stats_for_unit_all_campaigns(session: AsyncSession, unit_id: int) -> List[Dict]:
    """
    آمار کلی یک واحد روی همهٔ کمپین‌ها:
    خروجی: [{campaign_id, campaign_name, platform, count}, ...]
    """
    q = (
        select(
            Campaign.id.label("campaign_id"),
            Campaign.name.label("campaign_name"),
            Report.platform.label("platform"),
            func.count(ReportItem.id).label("count"),
        )
        .join(Report, Report.campaign_id == Campaign.id)
        .join(ReportItem, ReportItem.report_id == Report.id, isouter=True)
        .where(Report.unit_id_owner == unit_id)
        .group_by(Campaign.id, Campaign.name, Report.platform)
        .order_by(Campaign.id.desc(), Report.platform.asc())
    )
    rows = (await session.execute(q)).all()
    return [
        {
            "campaign_id": r.campaign_id,
            "campaign_name": r.campaign_name,
            "platform": r.platform,
            "count": r.count or 0,
        }
        for r in rows
    ]

async def fetch_unit_campaign_items(session: AsyncSession, unit_id: int, campaign_id: int):
    """
    آیتم‌های فایل برای یک واحد در یک کمپین
    خروجی: [(file_path, platform, user_id, campaign_id, campaign_name)]
    """
    q = (
        select(ReportItem.file_path, ReportItem.platform, Report.user_id, Campaign.id, Campaign.name)
        .join(Report, ReportItem.report_id == Report.id)
        .join(Campaign, Campaign.id == Report.campaign_id)
        .where(Report.unit_id_owner == unit_id, Report.campaign_id == campaign_id)
        .order_by(ReportItem.id)
    )
    return [(fp, plat, uid, cid, cname) for fp, plat, uid, cid, cname in (await session.execute(q)).all()]

async def fetch_unit_all_items(session: AsyncSession, unit_id: int):
    """
    آیتم‌های فایل برای یک واحد روی همه کمپین‌ها
    خروجی: [(file_path, platform, user_id, campaign_id, campaign_name)]
    """
    q = (
        select(ReportItem.file_path, ReportItem.platform, Report.user_id, Campaign.id, Campaign.name)
        .join(Report, ReportItem.report_id == Report.id)
        .join(Campaign, Campaign.id == Report.campaign_id)
        .where(Report.unit_id_owner == unit_id)
        .order_by(Campaign.id.desc(), ReportItem.id)
    )
    return [(fp, plat, uid, cid, cname) for fp, plat, uid, cid, cname in (await session.execute(q)).all()]
