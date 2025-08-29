# -*- coding: utf-8 -*-
from __future__ import annotations
from sqlalchemy import (
    Integer, BigInteger, String, Text, Boolean, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base

class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashtag: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(255))
    platforms: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list (string)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # این‌ها به آیدی‌های تلگرام/ادمین مربوط می‌شوند → BigInteger
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    admin_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    created_at: Mapped[str] = mapped_column(String(50), nullable=False)

    root_campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    unit_id_owner: Mapped[int | None] = mapped_column(Integer, index=True)
    config_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))  # ACTIVE | CLOSED ...

    reports: Mapped[list["Report"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # آیدی یوزر تلگرام → BigInteger
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    city_id: Mapped[int | None] = mapped_column(Integer, index=True)

    unit_id_owner: Mapped[int | None] = mapped_column(Integer, index=True)
    submitted_to_campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    submitted_to_unit_id: Mapped[int | None] = mapped_column(Integer, index=True)
    summary_json: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["ReportItem"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    item_refs: Mapped[list["ReportItemRef"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    campaign: Mapped["Campaign"] = relationship(back_populates="reports")

class ReportItem(Base):
    __tablename__ = "report_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    file_id: Mapped[str | None] = mapped_column(String(256))
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)

    report: Mapped["Report"] = relationship(back_populates="items")

class ReportItemRef(Base):
    __tablename__ = "report_item_refs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    source_report_item_id: Mapped[int] = mapped_column(Integer, index=True)
    report: Mapped["Report"] = relationship(back_populates="item_refs")

class User(Base):
    __tablename__ = "users"
    # تلگرام user_id → BigInteger
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # ادمین مالک کاربر → BigInteger
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    display_name: Mapped[str | None] = mapped_column(String(255))
    city_id: Mapped[int | None] = mapped_column(Integer)
    unit_id_paygah: Mapped[int | None] = mapped_column(Integer)

class City(Base):
    __tablename__ = "cities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # ادمین ایجادکننده/مالک → BigInteger
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)

    created_at: Mapped[str] = mapped_column(String(50), nullable=False)

class Admin(Base):
    __tablename__ = "admins"
    # Telegram user id → BigInteger
    admin_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # SUPER | L1 | L2 ...

class AdminTree(Base):
    __tablename__ = "admin_tree"
    # هر دو به آیدی تلگرام اشاره دارند → BigInteger
    parent_admin_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    child_admin_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    __table_args__ = (UniqueConstraint("parent_admin_id", "child_admin_id"),)

class Unit(Base):
    __tablename__ = "units"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # COUNTRY|OSTAN|SHAHR|HOZE|PAYGAH
    parent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    __table_args__ = (Index("idx_units_parent", "parent_id"),)

class UnitAdmin(Base):
    __tablename__ = "unit_admins"
    unit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # admin_id تلگرام → BigInteger
    admin_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # OWNER | ASSISTANT

class CampaignCopy(Base):
    __tablename__ = "campaign_copies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)
    to_campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)
    from_unit_id: Mapped[int] = mapped_column(Integer, nullable=False)
    to_unit_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # ادمین کپی‌کننده (تلگرام) → BigInteger
    copied_by_admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    copied_at: Mapped[str] = mapped_column(String(50), nullable=False)
