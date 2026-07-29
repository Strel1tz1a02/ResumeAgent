"""Shared FastAPI dependency for repository sessions."""

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


async def get_repository_session() -> AsyncIterator[AsyncSession]:
    """Yield the current database's session without committing or rolling back."""
    from app.database import db

    async with db.session() as session:
        yield session
