"""Repository layer for database operations."""

from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Game, NotificationJob, Transaction, User, UserTransactionPreferences
from .models import (
    GameRecord,
    NotificationJobRecord,
    TransactionRecord,
    UserRecord,
    UserTransactionPreference,
)

logger = structlog.get_logger(__name__)


class Repository:
    """Repository for database operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository with a database session."""
        self.session = session

    # Game operations
    async def save_game(self, game: Game) -> None:
        """Save or update a game record."""
        try:
            # Check if game already exists
            result = await self.session.execute(
                select(GameRecord).where(GameRecord.game_id == game.game_id)
            )
            existing_game = result.scalar_one_or_none()

            if existing_game:
                # Update existing game
                existing_game.date = game.date  # type: ignore[assignment]
                existing_game.home_team = game.home_team  # type: ignore[assignment]
                existing_game.away_team = game.away_team  # type: ignore[assignment]
                existing_game.venue = game.venue  # type: ignore[assignment]
                existing_game.status = game.status.value  # type: ignore[assignment]
                existing_game.notification_sent = game.notification_sent  # type: ignore[assignment]
                existing_game.updated_at = datetime.now(UTC)  # type: ignore[assignment]

                logger.debug("Updated existing game", game_id=game.game_id)
            else:
                # Create new game record
                game_record = GameRecord(
                    game_id=game.game_id,
                    date=game.date,
                    home_team=game.home_team,
                    away_team=game.away_team,
                    venue=game.venue,
                    status=game.status.value,
                    notification_sent=game.notification_sent,
                )

                self.session.add(game_record)
                logger.debug("Created new game", game_id=game.game_id)

        except Exception as e:
            logger.error("Failed to save game", game_id=game.game_id, error=str(e))
            raise

    async def get_game(self, game_id: str) -> Game | None:
        """Get a game by ID."""
        try:
            result = await self.session.execute(
                select(GameRecord).where(GameRecord.game_id == game_id)
            )
            game_record = result.scalar_one_or_none()

            if game_record:
                return self._game_record_to_model(game_record)
            return None

        except Exception as e:
            logger.error("Failed to get game", game_id=game_id, error=str(e))
            raise

    async def get_current_games(self, within_hours: int = 2) -> list[Game]:
        """Get games that are currently in progress (started within the specified hours)."""
        from datetime import timedelta

        try:
            now = datetime.now(UTC)
            cutoff_time = now - timedelta(hours=within_hours)

            result = await self.session.execute(
                select(GameRecord)
                .where(
                    and_(
                        GameRecord.date >= cutoff_time,
                        GameRecord.date <= now,
                        GameRecord.status.in_(["scheduled", "live"])
                    )
                )
                .order_by(GameRecord.date.desc())
            )

            games = []
            for record in result.scalars():
                games.append(self._game_record_to_model(record))

            return games

        except Exception as e:
            logger.error("Failed to get current games", error=str(e))
            raise

    async def get_upcoming_games(self, limit: int = 10) -> list[Game]:
        """Get upcoming games that haven't been notified yet."""
        try:
            result = await self.session.execute(
                select(GameRecord)
                .where(
                    and_(
                        GameRecord.date > datetime.now(UTC),
                        ~GameRecord.notification_sent,
                        GameRecord.status == "scheduled"
                    )
                )
                .order_by(GameRecord.date)
                .limit(limit)
            )

            games = []
            for record in result.scalars():
                games.append(self._game_record_to_model(record))

            return games

        except Exception as e:
            logger.error("Failed to get upcoming games", error=str(e))
            raise

    async def mark_game_notified(self, game_id: str) -> None:
        """Mark a game as having been notified."""
        try:
            await self.session.execute(
                update(GameRecord)
                .where(GameRecord.game_id == game_id)
                .values(notification_sent=True, updated_at=datetime.utcnow())
            )

            logger.debug("Marked game as notified", game_id=game_id)

        except Exception as e:
            logger.error("Failed to mark game as notified", game_id=game_id, error=str(e))
            raise

    # Notification job operations
    async def save_notification_job(self, job: NotificationJob) -> None:
        """Save or update a notification job."""
        try:
            job_id = job.job_id

            # Check if job already exists
            result = await self.session.execute(
                select(NotificationJobRecord).where(NotificationJobRecord.id == job_id)
            )
            existing_job = result.scalar_one_or_none()

            if existing_job:
                # Update existing job
                existing_job.scheduled_time = job.scheduled_time  # type: ignore[assignment]
                existing_job.message = job.message  # type: ignore[assignment]
                existing_job.status = job.status.value  # type: ignore[assignment]
                existing_job.chat_id = job.chat_id  # type: ignore[assignment]
                existing_job.attempts = job.attempts  # type: ignore[assignment]
                existing_job.error_message = job.error_message  # type: ignore[assignment]
                existing_job.sent_at = job.sent_at  # type: ignore[assignment]

                logger.debug("Updated existing notification job", job_id=job_id)
            else:
                # Create new job record
                job_record = NotificationJobRecord(
                    id=job_id,
                    game_id=job.game_id,
                    scheduled_time=job.scheduled_time,
                    message=job.message,
                    status=job.status.value,
                    chat_id=job.chat_id,
                    attempts=job.attempts,
                    error_message=job.error_message,
                    sent_at=job.sent_at,
                )

                self.session.add(job_record)
                logger.debug("Created new notification job", job_id=job_id)

        except Exception as e:
            logger.error("Failed to save notification job", job_id=job.job_id, error=str(e))
            raise

    async def get_pending_jobs(self) -> list[NotificationJob]:
        """Get all pending notification jobs."""
        try:
            result = await self.session.execute(
                select(NotificationJobRecord)
                .where(NotificationJobRecord.status == "pending")
                .order_by(NotificationJobRecord.scheduled_time)
            )

            jobs = []
            for record in result.scalars():
                jobs.append(self._job_record_to_model(record))

            return jobs

        except Exception as e:
            logger.error("Failed to get pending jobs", error=str(e))
            raise

    # User operations
    async def save_user(self, user: User) -> None:
        """Save or update a user record."""
        try:
            # Check if user already exists
            result = await self.session.execute(
                select(UserRecord).where(UserRecord.chat_id == user.chat_id)
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                # Update existing user
                existing_user.username = user.username  # type: ignore[assignment]
                existing_user.first_name = user.first_name  # type: ignore[assignment]
                existing_user.last_name = user.last_name  # type: ignore[assignment]
                existing_user.subscribed = user.subscribed  # type: ignore[assignment]
                existing_user.timezone = user.timezone  # type: ignore[assignment]
                existing_user.last_seen = user.last_seen  # type: ignore[assignment]

                logger.debug("Updated existing user", chat_id=user.chat_id)
            else:
                # Create new user record
                user_record = UserRecord(
                    chat_id=user.chat_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    subscribed=user.subscribed,
                    timezone=user.timezone,
                    last_seen=user.last_seen,
                )

                self.session.add(user_record)
                logger.debug("Created new user", chat_id=user.chat_id)

        except Exception as e:
            logger.error("Failed to save user", chat_id=user.chat_id, error=str(e))
            raise

    async def get_subscribed_users(self) -> list[User]:
        """Get all subscribed users."""
        try:
            result = await self.session.execute(
                select(UserRecord).where(UserRecord.subscribed)
            )

            users = []
            for record in result.scalars():
                users.append(self._user_record_to_model(record))

            return users

        except Exception as e:
            logger.error("Failed to get subscribed users", error=str(e))
            raise

    # Conversion methods
    def _game_record_to_model(self, record: GameRecord) -> Game:
        """Convert a GameRecord to a Game model."""
        from ..models import GameStatus

        return Game(
            game_id=record.game_id,  # type: ignore[arg-type]
            date=record.date,  # type: ignore[arg-type]
            home_team=record.home_team,  # type: ignore[arg-type]
            away_team=record.away_team,  # type: ignore[arg-type]
            venue=record.venue or "",  # type: ignore[arg-type]
            status=GameStatus(record.status),
            notification_sent=record.notification_sent,  # type: ignore[arg-type]
            created_at=record.created_at,  # type: ignore[arg-type]
            updated_at=record.updated_at,  # type: ignore[arg-type]
        )

    def _job_record_to_model(self, record: NotificationJobRecord) -> NotificationJob:
        """Convert a NotificationJobRecord to a NotificationJob model."""
        from ..models import NotificationStatus

        return NotificationJob(
            id=record.id,  # type: ignore[arg-type]
            game_id=record.game_id,  # type: ignore[arg-type]
            scheduled_time=record.scheduled_time,  # type: ignore[arg-type]
            message=record.message,  # type: ignore[arg-type]
            status=NotificationStatus(record.status),
            chat_id=record.chat_id,  # type: ignore[arg-type]
            attempts=record.attempts,  # type: ignore[arg-type]
            error_message=record.error_message,  # type: ignore[arg-type]
            created_at=record.created_at,  # type: ignore[arg-type]
            sent_at=record.sent_at,  # type: ignore[arg-type]
        )

    def _user_record_to_model(self, record: UserRecord) -> User:
        """Convert a UserRecord to a User model."""
        return User(
            chat_id=record.chat_id,  # type: ignore[arg-type]
            username=record.username,  # type: ignore[arg-type]
            first_name=record.first_name,  # type: ignore[arg-type]
            last_name=record.last_name,  # type: ignore[arg-type]
            subscribed=record.subscribed,  # type: ignore[arg-type]
            timezone=record.timezone,  # type: ignore[arg-type]
            created_at=record.created_at,  # type: ignore[arg-type]
            last_seen=record.last_seen,  # type: ignore[arg-type]
        )

    # Transaction operations
    async def save_transaction(self, transaction: Transaction) -> None:
        """Save or update a transaction record."""
        try:
            # Check if transaction already exists
            result = await self.session.execute(
                select(TransactionRecord).where(TransactionRecord.transaction_id == transaction.transaction_id)
            )
            existing_transaction = result.scalar_one_or_none()

            if existing_transaction:
                # Update existing transaction
                existing_transaction.person_id = transaction.person_id  # type: ignore[assignment]
                existing_transaction.person_name = transaction.person_name  # type: ignore[assignment]
                existing_transaction.from_team_id = transaction.from_team_id  # type: ignore[assignment]
                existing_transaction.from_team_name = transaction.from_team_name  # type: ignore[assignment]
                existing_transaction.to_team_id = transaction.to_team_id  # type: ignore[assignment]
                existing_transaction.to_team_name = transaction.to_team_name  # type: ignore[assignment]
                existing_transaction.transaction_date = transaction.transaction_date  # type: ignore[assignment]
                existing_transaction.effective_date = transaction.effective_date  # type: ignore[assignment]
                existing_transaction.resolution_date = transaction.resolution_date  # type: ignore[assignment]
                existing_transaction.type_code = transaction.type_code  # type: ignore[assignment]
                existing_transaction.type_description = transaction.type_description  # type: ignore[assignment]
                existing_transaction.description = transaction.description  # type: ignore[assignment]

                logger.debug("Updated existing transaction", transaction_id=transaction.transaction_id)
            else:
                # Create new transaction record
                transaction_record = TransactionRecord(
                    transaction_id=transaction.transaction_id,
                    person_id=transaction.person_id,
                    person_name=transaction.person_name,
                    from_team_id=transaction.from_team_id,
                    from_team_name=transaction.from_team_name,
                    to_team_id=transaction.to_team_id,
                    to_team_name=transaction.to_team_name,
                    transaction_date=transaction.transaction_date,
                    effective_date=transaction.effective_date,
                    resolution_date=transaction.resolution_date,
                    type_code=transaction.type_code,
                    type_description=transaction.type_description,
                    description=transaction.description,
                    notification_sent=False
                )

                self.session.add(transaction_record)
                logger.debug("Created new transaction", transaction_id=transaction.transaction_id)

            await self.session.commit()

        except Exception as e:
            await self.session.rollback()
            logger.error("Failed to save transaction", transaction_id=transaction.transaction_id, error=str(e))
            raise

    async def get_new_transactions(self) -> list[Transaction]:
        """Get transactions that haven't had notifications sent yet."""
        try:
            result = await self.session.execute(
                select(TransactionRecord).where(
                    and_(
                        TransactionRecord.notification_sent == False,  # noqa: E712
                        TransactionRecord.transaction_date >= datetime(2025, 1, 1).date()  # Only recent transactions
                    )
                ).order_by(TransactionRecord.transaction_date.desc())
            )

            transactions = []
            for record in result.scalars():
                transactions.append(self._transaction_record_to_model(record))

            return transactions

        except Exception as e:
            logger.error("Failed to get new transactions", error=str(e))
            raise

    async def mark_transaction_notified(self, transaction_id: int) -> None:
        """Mark a transaction as having been notified."""
        try:
            await self.session.execute(
                update(TransactionRecord)
                .where(TransactionRecord.transaction_id == transaction_id)
                .values(notification_sent=True)
            )
            await self.session.commit()

            logger.debug("Marked transaction as notified", transaction_id=transaction_id)

        except Exception as e:
            await self.session.rollback()
            logger.error("Failed to mark transaction as notified", transaction_id=transaction_id, error=str(e))
            raise

    # User transaction preferences operations
    async def save_user_transaction_preferences(self, preferences: UserTransactionPreferences) -> None:
        """Save or update user transaction preferences."""
        try:
            # Check if preferences already exist
            result = await self.session.execute(
                select(UserTransactionPreference).where(UserTransactionPreference.chat_id == preferences.chat_id)
            )
            existing_preferences = result.scalar_one_or_none()

            if existing_preferences:
                # Update existing preferences
                existing_preferences.trades = preferences.trades  # type: ignore[assignment]
                existing_preferences.signings = preferences.signings  # type: ignore[assignment]
                existing_preferences.recalls = preferences.recalls  # type: ignore[assignment]
                existing_preferences.options = preferences.options  # type: ignore[assignment]
                existing_preferences.injuries = preferences.injuries  # type: ignore[assignment]
                existing_preferences.activations = preferences.activations  # type: ignore[assignment]
                existing_preferences.releases = preferences.releases  # type: ignore[assignment]
                existing_preferences.status_changes = preferences.status_changes  # type: ignore[assignment]
                existing_preferences.other = preferences.other  # type: ignore[assignment]
                existing_preferences.major_league_only = preferences.major_league_only  # type: ignore[assignment]
                existing_preferences.updated_at = datetime.now(UTC)  # type: ignore[assignment]

                logger.debug("Updated user transaction preferences", chat_id=preferences.chat_id)
            else:
                # Create new preferences record
                preferences_record = UserTransactionPreference(
                    chat_id=preferences.chat_id,
                    trades=preferences.trades,
                    signings=preferences.signings,
                    recalls=preferences.recalls,
                    options=preferences.options,
                    injuries=preferences.injuries,
                    activations=preferences.activations,
                    releases=preferences.releases,
                    status_changes=preferences.status_changes,
                    other=preferences.other,
                    major_league_only=preferences.major_league_only
                )

                self.session.add(preferences_record)
                logger.debug("Created new user transaction preferences", chat_id=preferences.chat_id)

            await self.session.commit()

        except Exception as e:
            await self.session.rollback()
            logger.error("Failed to save user transaction preferences", chat_id=preferences.chat_id, error=str(e))
            raise

    async def get_user_transaction_preferences(self, chat_id: int) -> UserTransactionPreferences:
        """Get user transaction preferences."""
        try:
            result = await self.session.execute(
                select(UserTransactionPreference).where(UserTransactionPreference.chat_id == chat_id)
            )
            preferences_record = result.scalar_one_or_none()

            if preferences_record:
                return self._user_preferences_record_to_model(preferences_record)
            else:
                # Return default preferences if none exist
                return UserTransactionPreferences(chat_id=chat_id)

        except Exception as e:
            logger.error("Failed to get user transaction preferences", chat_id=chat_id, error=str(e))
            # Return default preferences on error
            return UserTransactionPreferences(chat_id=chat_id)

    async def get_users_for_transaction_notification(self, transaction: Transaction) -> list[tuple[User, UserTransactionPreferences]]:
        """Get users who should be notified about a specific transaction."""
        try:
            # Get all subscribed users with their preferences
            result = await self.session.execute(
                select(UserRecord, UserTransactionPreference)
                .outerjoin(UserTransactionPreference, UserRecord.chat_id == UserTransactionPreference.chat_id)
                .where(UserRecord.subscribed == True)  # noqa: E712
            )

            user_preferences = []
            for user_record, pref_record in result.all():
                user = self._user_record_to_model(user_record)

                if pref_record:
                    preferences = self._user_preferences_record_to_model(pref_record)
                else:
                    # Use default preferences if none exist
                    preferences = UserTransactionPreferences(chat_id=user.chat_id)

                # Check if user should be notified for this transaction
                if preferences.should_notify_for_transaction(transaction.transaction_type, transaction.description):
                    user_preferences.append((user, preferences))

            return user_preferences

        except Exception as e:
            logger.error("Failed to get users for transaction notification", error=str(e))
            return []

    def _transaction_record_to_model(self, record: TransactionRecord) -> Transaction:
        """Convert a TransactionRecord to a Transaction model."""
        return Transaction(
            transaction_id=record.transaction_id,  # type: ignore[arg-type]
            person_id=record.person_id,  # type: ignore[arg-type]
            person_name=record.person_name,  # type: ignore[arg-type]
            from_team_id=record.from_team_id,  # type: ignore[arg-type]
            from_team_name=record.from_team_name,  # type: ignore[arg-type]
            to_team_id=record.to_team_id,  # type: ignore[arg-type]
            to_team_name=record.to_team_name,  # type: ignore[arg-type]
            transaction_date=record.transaction_date,  # type: ignore[arg-type]
            effective_date=record.effective_date,  # type: ignore[arg-type]
            resolution_date=record.resolution_date,  # type: ignore[arg-type]
            type_code=record.type_code,  # type: ignore[arg-type]
            type_description=record.type_description,  # type: ignore[arg-type]
            description=record.description,  # type: ignore[arg-type]
        )

    def _user_preferences_record_to_model(self, record: UserTransactionPreference) -> UserTransactionPreferences:
        """Convert a UserTransactionPreference to a UserTransactionPreferences model."""
        return UserTransactionPreferences(
            chat_id=record.chat_id,  # type: ignore[arg-type]
            trades=record.trades,  # type: ignore[arg-type]
            signings=record.signings,  # type: ignore[arg-type]
            recalls=record.recalls,  # type: ignore[arg-type]
            options=record.options,  # type: ignore[arg-type]
            injuries=record.injuries,  # type: ignore[arg-type]
            activations=record.activations,  # type: ignore[arg-type]
            releases=record.releases,  # type: ignore[arg-type]
            status_changes=record.status_changes,  # type: ignore[arg-type]
            other=record.other,  # type: ignore[arg-type]
            major_league_only=record.major_league_only,  # type: ignore[arg-type]
        )
