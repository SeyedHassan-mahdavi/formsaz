# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///campaign_bot.sqlite")

class Base(DeclarativeBase):
    pass

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine, expire_on_commit=False, autoflush=False
)

async def init_db():
    # dev-only: ایجاد جداول بر اساس مدل‌ها (برای Production از Alembic استفاده کنید)
    from models import (
        Campaign, Report, ReportItem, User, City,
        Admin, AdminTree, Unit, UnitAdmin, CampaignCopy, ReportItemRef
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
