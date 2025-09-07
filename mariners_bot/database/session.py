"""Database session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from ..config import Settings
from .models import Base

logger = structlog.get_logger(__name__)


class DatabaseSession:
    """Database session manager."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the database session manager."""
        self.settings = settings
        self.database_url = settings.database_url

        # Create async engine for main operations
        if self.database_url.startswith("sqlite"):
            # Convert sqlite URL to async version
            async_url = self.database_url.replace("sqlite:///", "sqlite+aiosqlite:///")
            self.async_engine = create_async_engine(
                async_url,
                echo=settings.debug,
                future=True,
            )
        else:
            # For other databases, use asyncpg
            self.async_engine = create_async_engine(
                self.database_url,
                echo=settings.debug,
                future=True,
            )

        # Create sync engine for migrations
        self.sync_engine = create_engine(
            self.database_url,
            echo=settings.debug,
            future=True,
        )

        # Session factories
        self.async_session_factory = async_sessionmaker(
            self.async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        self.sync_session_factory = sessionmaker(
            self.sync_engine,
            expire_on_commit=False,
        )

    async def create_tables(self) -> None:
        """Create all database tables."""
        logger.info("Creating database tables")

        try:
            async with self.async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created successfully")

        except Exception as e:
            logger.error("Failed to create database tables", error=str(e))
            raise

    async def drop_tables(self) -> None:
        """Drop all database tables."""
        logger.warning("Dropping all database tables")

        try:
            async with self.async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            logger.info("Database tables dropped successfully")

        except Exception as e:
            logger.error("Failed to drop database tables", error=str(e))
            raise

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async database session."""
        async with self.async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def close(self) -> None:
        """Close database connections."""
        logger.info("Closing database connections")
        await self.async_engine.dispose()
        self.sync_engine.dispose()


# Global database session instance
_db_session: DatabaseSession | None = None


def get_database_session(settings: Settings) -> DatabaseSession:
    """Get the global database session instance."""
    global _db_session

    if _db_session is None:
        _db_session = DatabaseSession(settings)

    return _db_session
