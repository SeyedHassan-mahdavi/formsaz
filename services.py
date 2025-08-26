# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Tuple, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import Campaign, CampaignCopy, Report, ReportItem, ReportItemRef, Unit
from crud import (
    child_units, parent_unit_id, get_campaign, platforms_from_json, create_campaign_v2
)
from utils import now_iso

async def copy_campaign_one_level(session: AsyncSession, src_campaign_id: int, target_unit_ids: List[int], by_admin_id: int) -> List[int]:
    src = await session.get(Campaign, src_campaign_id)
    if not src:
        return []
    root_id = src.root_campaign_id or src.id
    owner_unit_id = src.unit_id_owner
    new_ids: list[int] = []

    # فقط فرزندان مستقیم
    childs = await child_units(session, owner_unit_id)
    children_ids = {u.id for u in childs}

    # آماده‌سازی config
    try:
        import json
        cfg = json.loads(src.config_json) if src.config_json else {}
    except Exception:
        cfg = {}

    # جلوگیری از دوباره‌کپی (اگر قبلاً نسخه‌ای از همین root روی همان واحد مقصد هست)
    from sqlalchemy import select
    for tuid in target_unit_ids:
        if tuid not in children_ids:
            continue

        existing = await session.execute(
            select(Campaign.id).where(Campaign.unit_id_owner == tuid, Campaign.root_campaign_id == root_id)
        )
        if existing.scalar_one_or_none():
            # قبلاً کپی شده؛ رد می‌کنیم
            continue

        new_id = await create_campaign_v2(
            session=session,
            owner_unit_id=tuid,
            owner_admin_id=by_admin_id,
            name=src.name,
            platforms=platforms_from_json(src.platforms),  # ← لیست
            description=src.description,
            hashtag=src.hashtag,
            city_label=src.city,
            config=cfg,  # ← تنظیمات کمپین
        )
        new_ids.append(new_id)

        session.add(CampaignCopy(
            from_campaign_id=src.id, to_campaign_id=new_id,
            from_unit_id=owner_unit_id, to_unit_id=tuid,
            copied_by_admin_id=by_admin_id, copied_at=now_iso()
        ))

    return new_ids

async def submit_report_up(session: AsyncSession, current_unit_id: int, current_campaign_id: int,
                           refs_item_ids: List[int], extra_items: List[Tuple[str,str]],
                           by_admin_id: int, summary: dict) -> Optional[int]:
    up_uid = await parent_unit_id(session, current_unit_id)
    if not up_uid:
        return None
    cur = await session.execute(select(Campaign.root_campaign_id).where(Campaign.id==current_campaign_id))
    root_id = cur.scalar_one_or_none() or current_campaign_id

    upc = await session.execute(
        select(Campaign.id).where(Campaign.root_campaign_id==root_id, Campaign.unit_id_owner==up_uid)
    )
    up_cid = upc.scalar_one_or_none()
    if not up_cid:
        return None

    from utils import now_iso
    import json
    r = Report(
        user_id=by_admin_id, campaign_id=up_cid, platform='non_telegram', created_at=now_iso(), city_id=None,
        unit_id_owner=current_unit_id, submitted_to_campaign_id=up_cid, submitted_to_unit_id=up_uid,
        summary_json=json.dumps(summary, ensure_ascii=False)
    )
    session.add(r); await session.flush()
    rid = r.id

    for iid in refs_item_ids or []:
        session.add(ReportItemRef(report_id=rid, source_report_item_id=iid))
    for file_id, file_path in extra_items or []:
        session.add(ReportItem(report_id=rid, file_id=file_id, file_path=file_path, created_at=now_iso()))
    return rid
