"""Tests for transaction database operations."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mariners_bot.database.models import Base
from mariners_bot.database.repository import Repository
from mariners_bot.models.transaction import Transaction, TransactionType
from mariners_bot.models.user_preferences import UserTransactionPreferences


@pytest.fixture
async def test_db_session() -> AsyncIterator[AsyncSession]:
    """Create a test database session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def sample_transaction() -> Transaction:
    """Create a sample transaction for testing."""
    return Transaction(
        transaction_id=123456,
        person_id=789,
        person_name="Test Player",
        to_team_id=136,
        to_team_name="Seattle Mariners",
        transaction_date=date.today(),
        effective_date=date.today(),
        type_code="SFA",
        type_description="Signed as Free Agent",
        description="Seattle Mariners signed free agent Test Player."
    )


@pytest.fixture
def sample_preferences() -> UserTransactionPreferences:
    """Create sample user preferences for testing."""
    return UserTransactionPreferences(
        chat_id=12345,
        trades=True,
        signings=True,
        injuries=False,
        major_league_only=True
    )


class TestTransactionRepository:
    """Test transaction database operations."""

    @pytest.mark.asyncio
    async def test_save_new_transaction(self, test_db_session: AsyncSession, sample_transaction: Transaction) -> None:
        """Test saving a new transaction."""
        repository = Repository(test_db_session)

        await repository.save_transaction(sample_transaction)

        # Verify transaction was saved
        result = await test_db_session.execute(
            text("SELECT * FROM transactions WHERE transaction_id = 123456")
        )
        row = result.fetchone()

        assert row is not None
        assert row[0] == 123456  # transaction_id
        assert row[2] == "Test Player"  # person_name
        assert row[5] == 136  # to_team_id

    @pytest.mark.asyncio
    async def test_save_update_existing_transaction(self, test_db_session: AsyncSession, sample_transaction: Transaction) -> None:
        """Test updating an existing transaction."""
        repository = Repository(test_db_session)

        # Save initial transaction
        await repository.save_transaction(sample_transaction)

        # Update transaction description
        sample_transaction.description = "Updated description"
        await repository.save_transaction(sample_transaction)

        # Verify update
        result = await test_db_session.execute(
            text("SELECT description FROM transactions WHERE transaction_id = 123456")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "Updated description"

    @pytest.mark.asyncio
    async def test_get_new_transactions(self, test_db_session: AsyncSession, sample_transaction: Transaction) -> None:
        """Test getting new transactions that haven't been notified."""
        repository = Repository(test_db_session)

        # Save transaction
        await repository.save_transaction(sample_transaction)

        # Get new transactions
        new_transactions = await repository.get_new_transactions()

        assert len(new_transactions) == 1
        assert new_transactions[0].transaction_id == 123456
        assert new_transactions[0].person_name == "Test Player"

    @pytest.mark.asyncio
    async def test_get_new_transactions_excludes_notified(self, test_db_session: AsyncSession, sample_transaction: Transaction) -> None:
        """Test that get_new_transactions excludes already notified transactions."""
        repository = Repository(test_db_session)

        # Save transaction
        await repository.save_transaction(sample_transaction)

        # Mark as notified
        await repository.mark_transaction_notified(sample_transaction.transaction_id)

        # Get new transactions
        new_transactions = await repository.get_new_transactions()

        assert len(new_transactions) == 0

    @pytest.mark.asyncio
    async def test_mark_transaction_notified(self, test_db_session: AsyncSession, sample_transaction: Transaction) -> None:
        """Test marking a transaction as notified."""
        repository = Repository(test_db_session)

        # Save transaction
        await repository.save_transaction(sample_transaction)

        # Mark as notified
        await repository.mark_transaction_notified(sample_transaction.transaction_id)

        # Verify notification_sent flag
        result = await test_db_session.execute(
            text("SELECT notification_sent FROM transactions WHERE transaction_id = 123456")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == 1  # SQLite returns 1 for True

    @pytest.mark.asyncio
    async def test_save_user_transaction_preferences_new(self, test_db_session: AsyncSession, sample_preferences: UserTransactionPreferences) -> None:
        """Test saving new user transaction preferences."""
        repository = Repository(test_db_session)

        await repository.save_user_transaction_preferences(sample_preferences)

        # Verify preferences were saved
        result = await test_db_session.execute(
            text("SELECT * FROM user_transaction_preferences WHERE chat_id = 12345")
        )
        row = result.fetchone()

        assert row is not None
        assert row[1] == 12345  # chat_id
        assert row[2] == 1  # trades (SQLite returns 1 for True)
        assert row[3] == 1  # signings (SQLite returns 1 for True)
        assert row[6] == 0  # injuries (SQLite returns 0 for False)

    @pytest.mark.asyncio
    async def test_save_user_transaction_preferences_update(self, test_db_session: AsyncSession, sample_preferences: UserTransactionPreferences) -> None:
        """Test updating existing user transaction preferences."""
        repository = Repository(test_db_session)

        # Save initial preferences
        await repository.save_user_transaction_preferences(sample_preferences)

        # Update preferences
        sample_preferences.trades = False
        sample_preferences.injuries = True
        await repository.save_user_transaction_preferences(sample_preferences)

        # Verify update
        result = await test_db_session.execute(
            text("SELECT trades, injuries FROM user_transaction_preferences WHERE chat_id = 12345")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == 0  # trades (SQLite returns 0 for False)
        assert row[1] == 1  # injuries (SQLite returns 1 for True)

    @pytest.mark.asyncio
    async def test_get_user_transaction_preferences_existing(self, test_db_session: AsyncSession, sample_preferences: UserTransactionPreferences) -> None:
        """Test getting existing user transaction preferences."""
        repository = Repository(test_db_session)

        # Save preferences
        await repository.save_user_transaction_preferences(sample_preferences)

        # Retrieve preferences
        retrieved_prefs = await repository.get_user_transaction_preferences(12345)

        assert retrieved_prefs.chat_id == 12345
        assert retrieved_prefs.trades is True
        assert retrieved_prefs.signings is True
        assert retrieved_prefs.injuries is False

    @pytest.mark.asyncio
    async def test_get_user_transaction_preferences_default(self, test_db_session: AsyncSession) -> None:
        """Test getting default user transaction preferences for non-existing user."""
        repository = Repository(test_db_session)

        # Retrieve preferences for non-existing user
        retrieved_prefs = await repository.get_user_transaction_preferences(99999)

        # Should return default preferences
        assert retrieved_prefs.chat_id == 99999
        assert retrieved_prefs.trades is True  # Default value
        assert retrieved_prefs.signings is True  # Default value
        assert retrieved_prefs.major_league_only is True  # Default value

    @pytest.mark.asyncio
    async def test_get_users_for_transaction_notification(self, test_db_session: AsyncSession) -> None:
        """Test getting users who should be notified for a specific transaction."""
        repository = Repository(test_db_session)

        # Create a user record (simplified for test)
        from mariners_bot.database.models import UserRecord
        user_record = UserRecord(
            chat_id=12345,
            username="testuser",
            subscribed=True
        )
        test_db_session.add(user_record)

        # Create preferences that should match a trade
        prefs = UserTransactionPreferences(
            chat_id=12345,
            trades=True,
            signings=False
        )
        await repository.save_user_transaction_preferences(prefs)

        # Create a trade transaction
        trade_transaction = Transaction(
            transaction_id=1,
            person_id=1,
            person_name="Trade Player",
            from_team_id=136,
            from_team_name="Seattle Mariners",
            to_team_id=137,
            to_team_name="San Francisco Giants",
            transaction_date=date.today(),
            type_code="TR",
            type_description="Trade",
            description="Seattle Mariners traded player."
        )

        await test_db_session.commit()

        # Get users for notification
        users_prefs = await repository.get_users_for_transaction_notification(trade_transaction)

        assert len(users_prefs) == 1
        user, preferences = users_prefs[0]
        assert user.chat_id == 12345
        assert preferences.trades is True

    @pytest.mark.asyncio
    async def test_get_users_for_transaction_notification_filtered(self, test_db_session: AsyncSession) -> None:
        """Test that users are filtered based on preferences."""
        repository = Repository(test_db_session)

        # Create a user record
        from mariners_bot.database.models import UserRecord
        user_record = UserRecord(
            chat_id=12345,
            username="testuser",
            subscribed=True
        )
        test_db_session.add(user_record)

        # Create preferences that should NOT match a trade
        prefs = UserTransactionPreferences(
            chat_id=12345,
            trades=False,  # Disabled
            signings=True
        )
        await repository.save_user_transaction_preferences(prefs)

        # Create a trade transaction
        trade_transaction = Transaction(
            transaction_id=1,
            person_id=1,
            person_name="Trade Player",
            transaction_date=date.today(),
            type_code="TR",
            type_description="Trade",
            description="Trade transaction."
        )

        await test_db_session.commit()

        # Get users for notification
        users_prefs = await repository.get_users_for_transaction_notification(trade_transaction)

        # Should be empty since user disabled trade notifications
        assert len(users_prefs) == 0

    @pytest.mark.asyncio
    async def test_transaction_record_to_model_conversion(self, test_db_session: AsyncSession, sample_transaction: Transaction) -> None:
        """Test conversion from database record to model."""
        repository = Repository(test_db_session)

        # Save transaction
        await repository.save_transaction(sample_transaction)

        # Get new transactions (which uses the conversion)
        new_transactions = await repository.get_new_transactions()

        transaction = new_transactions[0]
        assert isinstance(transaction, Transaction)
        assert transaction.transaction_id == sample_transaction.transaction_id
        assert transaction.person_name == sample_transaction.person_name
        assert transaction.transaction_type == TransactionType.SIGNED_FREE_AGENT

    @pytest.mark.asyncio
    async def test_user_preferences_record_to_model_conversion(self, test_db_session: AsyncSession, sample_preferences: UserTransactionPreferences) -> None:
        """Test conversion from preferences record to model."""
        repository = Repository(test_db_session)

        # Save preferences
        await repository.save_user_transaction_preferences(sample_preferences)

        # Retrieve preferences (which uses the conversion)
        retrieved_prefs = await repository.get_user_transaction_preferences(sample_preferences.chat_id)

        assert isinstance(retrieved_prefs, UserTransactionPreferences)
        assert retrieved_prefs.chat_id == sample_preferences.chat_id
        assert retrieved_prefs.trades == sample_preferences.trades
        assert retrieved_prefs.injuries == sample_preferences.injuries
