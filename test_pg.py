import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
import sqlalchemy as sa

url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://appuser:app-pass@localhost:5432/appdb"
)
engine = create_async_engine(url, echo=True)

async def main():
    async with engine.begin() as conn:
        result = await conn.execute(sa.text("SELECT 1"))
        print("Result =", result.scalar())

asyncio.run(main())
