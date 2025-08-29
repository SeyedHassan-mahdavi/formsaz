# alembic/env.py
from __future__ import annotations
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
import os, sys, re

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- fix sys.path to project root ---
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# فقط یکبار Base و ماژول models را ایمپورت کن
from database import Base
import models  # noqa: F401  (صرفاً برای پر کردن Base.metadata)
target_metadata = Base.metadata

def _sync_url(async_url: str) -> str:
    # برای sqlite/pg async => sync
    return re.sub(r'\+asyncpg', '', async_url).replace('aiosqlite', 'pysqlite')

def run_migrations_offline():
    raw_url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    url = _sync_url(raw_url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    ini_section = config.get_section(config.config_ini_section)
    raw_url = os.getenv("DATABASE_URL") or ini_section["sqlalchemy.url"]
    ini_section["sqlalchemy.url"] = _sync_url(raw_url)
    connectable = engine_from_config(
        ini_section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
