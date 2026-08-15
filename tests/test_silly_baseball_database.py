"""Tests for silly-baseball dedup and user opt-in repository operations."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mariners_bot.database.models import Base
from mariners_bot.database.repository import Repository
from mariners_bot.models import User


@pytest.fixture
async def test_db_session() -> AsyncIterator[AsyncSession]:
    """Create a test database session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        yield session

    await engine.dispose()


class TestSillyBaseballAlertDedup:
    async def test_first_claim_succeeds(self, test_db_session: AsyncSession) -> None:
        repo = Repository(test_db_session)
        claimed = await repo.try_record_silly_baseball_alert(game_pk=1001, alert_type="blowout")
        assert claimed is True

    async def test_second_claim_for_same_game_and_type_fails(self, test_db_session: AsyncSession) -> None:
        repo = Repository(test_db_session)
        await repo.try_record_silly_baseball_alert(game_pk=1001, alert_type="blowout")
        claimed_again = await repo.try_record_silly_baseball_alert(game_pk=1001, alert_type="blowout")
        assert claimed_again is False

    async def test_different_alert_types_for_same_game_both_claim(self, test_db_session: AsyncSession) -> None:
        repo = Repository(test_db_session)
        assert await repo.try_record_silly_baseball_alert(1001, "blowout") is True
        assert await repo.try_record_silly_baseball_alert(1001, "extra_innings") is True

    async def test_same_alert_type_for_different_games_both_claim(self, test_db_session: AsyncSession) -> None:
        repo = Repository(test_db_session)
        assert await repo.try_record_silly_baseball_alert(1001, "blowout") is True
        assert await repo.try_record_silly_baseball_alert(1002, "blowout") is True

    async def test_session_remains_usable_after_a_failed_claim(self, test_db_session: AsyncSession) -> None:
        """A rejected claim shouldn't poison the session for subsequent operations."""
        repo = Repository(test_db_session)
        await repo.try_record_silly_baseball_alert(1001, "blowout")
        await repo.try_record_silly_baseball_alert(1001, "blowout")  # rejected

        # Session should still be usable afterwards.
        assert await repo.try_record_silly_baseball_alert(1002, "blowout") is True


class TestSillyBaseballOptIn:
    async def test_defaults_to_opted_out(self, test_db_session: AsyncSession) -> None:
        repo = Repository(test_db_session)
        await repo.save_user(User(chat_id=555))

        opted_in = await repo.get_users_opted_into_silly_baseball()
        assert opted_in == []

    async def test_set_silly_baseball_alerts_opts_a_user_in(self, test_db_session: AsyncSession) -> None:
        repo = Repository(test_db_session)
        await repo.save_user(User(chat_id=555))

        result = await repo.set_silly_baseball_alerts(555, True)
        assert result is True

        opted_in = await repo.get_users_opted_into_silly_baseball()
        assert [u.chat_id for u in opted_in] == [555]

    async def test_save_user_does_not_clobber_opt_in(self, test_db_session: AsyncSession) -> None:
        """Regression guard: re-saving a User (e.g. on /subscribe) must not reset
        silly_baseball_alerts back to its model default of False."""
        repo = Repository(test_db_session)
        await repo.save_user(User(chat_id=555))
        await repo.set_silly_baseball_alerts(555, True)

        # Simulate a subsequent /subscribe call constructing a fresh User(...)
        # with the default silly_baseball_alerts=False and saving it.
        await repo.save_user(User(chat_id=555, subscribed=True))

        opted_in = await repo.get_users_opted_into_silly_baseball()
        assert [u.chat_id for u in opted_in] == [555]

    async def test_set_silly_baseball_alerts_creates_user_if_missing(self, test_db_session: AsyncSession) -> None:
        repo = Repository(test_db_session)
        result = await repo.set_silly_baseball_alerts(999, True)
        assert result is True

        opted_in = await repo.get_users_opted_into_silly_baseball()
        assert [u.chat_id for u in opted_in] == [999]
