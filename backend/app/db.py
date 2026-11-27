from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

def _async_url(url: str) -> str:
    """Hosts such as Render give plain postgres:// or postgresql:// URLs; SQLAlchemy needs the asyncpg driver name."""
    for old in ("postgres://", "postgresql://"):
        if url.startswith(old):
            return "postgresql+asyncpg://" + url[len(old):]
    return url


engine = create_async_engine(_async_url(settings.database_url), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with SessionLocal() as session:
        yield session
