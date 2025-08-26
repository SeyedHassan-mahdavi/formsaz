# scripts/seed_initial.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio, datetime
from dotenv import load_dotenv
from sqlalchemy import select
from database import SessionLocal, init_db  # init_db لازم نیست اگر فقط Alembic دارید
from models import Admin, Unit, UnitAdmin

load_dotenv()
# می‌تونی از .env مقدار دهی کنی: HARD_ADMINS=5018729099,123456789
def _parse_hard_admins() -> list[int]:
    raw = os.getenv("HARD_ADMINS", "").strip()
    ids: list[int] = []
    for p in raw.replace(";", ",").split(","):
        p = p.strip()
        if p.isdigit():
            ids.append(int(p))
    # اگر .env خالی بود، اینجا یک fallback بگذار
    if not ids:
        ids = [5018729099]  # همان ادمین هاردکد قبلی شما
    return ids

def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat()

async def main():
    # اگر در Production فقط از Alembic استفاده می‌کنی، این خط ضروری نیست.
    # گذاشتم که اگر دیتابیس خالی بود، در dev جداول ساخته شوند.
    await init_db()

    async with SessionLocal() as s:
        # 1) ادمین‌ها (SUPER)
        hard_admins = _parse_hard_admins()
        for aid in hard_admins:
            row = await s.get(Admin, aid)
            if not row:
                s.add(Admin(admin_id=aid, role="SUPER"))

        # 2) درخت واحد نمونه:
        # COUNTRY: Iran
        country = (await s.execute(
            select(Unit).where(Unit.type=="COUNTRY", Unit.name=="Iran")
        )).scalar_one_or_none()
        if not country:
            country = Unit(name="Iran", type="COUNTRY", parent_id=None, created_at=now_iso())
            s.add(country)
            await s.flush()  # تا country.id داشته باشیم

        # OSTAN: Tehran
        tehran = (await s.execute(
            select(Unit).where(Unit.type=="OSTAN", Unit.name=="Tehran")
        )).scalar_one_or_none()
        if not tehran:
            tehran = Unit(name="Tehran", type="OSTAN", parent_id=country.id, created_at=now_iso())
            s.add(tehran)
            await s.flush()

        # SHAHR: Tehran (city)
        tehran_city = (await s.execute(
            select(Unit).where(Unit.type=="SHAHR", Unit.name=="Tehran City")
        )).scalar_one_or_none()
        if not tehran_city:
            tehran_city = Unit(name="Tehran City", type="SHAHR", parent_id=tehran.id, created_at=now_iso())
            s.add(tehran_city)
            await s.flush()

        # HOZE نمونه
        hoze_1 = (await s.execute(
            select(Unit).where(Unit.type=="HOZE", Unit.name=="Hoze-1")
        )).scalar_one_or_none()
        if not hoze_1:
            hoze_1 = Unit(name="Hoze-1", type="HOZE", parent_id=tehran_city.id, created_at=now_iso())
            s.add(hoze_1)
            await s.flush()

        # PAYGAH نمونه
        paygah_1 = (await s.execute(
            select(Unit).where(Unit.type=="PAYGAH", Unit.name=="Paygah-1")
        )).scalar_one_or_none()
        if not paygah_1:
            paygah_1 = Unit(name="Paygah-1", type="PAYGAH", parent_id=hoze_1.id, created_at=now_iso())
            s.add(paygah_1)
            await s.flush()

        # 3) اتصال اولین ادمین به واحدها به عنوان OWNER
        first_admin = hard_admins[0]
        for uid in (country.id, tehran.id, tehran_city.id, hoze_1.id, paygah_1.id):
            exists = (await s.execute(
                select(UnitAdmin).where(UnitAdmin.unit_id==uid, UnitAdmin.admin_id==first_admin)
            )).scalar_one_or_none()
            if not exists:
                s.add(UnitAdmin(unit_id=uid, admin_id=first_admin, role="OWNER"))

        await s.commit()
    print("✅ Seed done.")

if __name__ == "__main__":
    asyncio.run(main())
