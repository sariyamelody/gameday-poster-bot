"""Repository layer for database operations."""

from datetime import datetime

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Game, NotificationJob, User
from .models import GameRecord, NotificationJobRecord, UserRecord

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
                existing_game.date = game.date
                existing_game.home_team = game.home_team
                existing_game.away_team = game.away_team
                existing_game.venue = game.venue
                existing_game.status = game.status.value
                existing_game.notification_sent = game.notification_sent
                existing_game.updated_at = datetime.utcnow()

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

    async def get_upcoming_games(self, limit: int = 10) -> list[Game]:
        """Get upcoming games that haven't been notified yet."""
        try:
            result = await self.session.execute(
                select(GameRecord)
                .where(
                    and_(
                        GameRecord.date > datetime.utcnow(),
                        not GameRecord.notification_sent,
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
                existing_job.scheduled_time = job.scheduled_time
                existing_job.message = job.message
                existing_job.status = job.status.value
                existing_job.chat_id = job.chat_id
                existing_job.attempts = job.attempts
                existing_job.error_message = job.error_message
                existing_job.sent_at = job.sent_at

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
                existing_user.username = user.username
                existing_user.first_name = user.first_name
                existing_user.last_name = user.last_name
                existing_user.subscribed = user.subscribed
                existing_user.timezone = user.timezone
                existing_user.last_seen = user.last_seen

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
            game_id=record.game_id,
            date=record.date,
            home_team=record.home_team,
            away_team=record.away_team,
            venue=record.venue or "",
            status=GameStatus(record.status),
            notification_sent=record.notification_sent,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _job_record_to_model(self, record: NotificationJobRecord) -> NotificationJob:
        """Convert a NotificationJobRecord to a NotificationJob model."""
        from ..models import NotificationStatus

        return NotificationJob(
            id=record.id,
            game_id=record.game_id,
            scheduled_time=record.scheduled_time,
            message=record.message,
            status=NotificationStatus(record.status),
            chat_id=record.chat_id,
            attempts=record.attempts,
            error_message=record.error_message,
            created_at=record.created_at,
            sent_at=record.sent_at,
        )

    def _user_record_to_model(self, record: UserRecord) -> User:
        """Convert a UserRecord to a User model."""
        return User(
            chat_id=record.chat_id,
            username=record.username,
            first_name=record.first_name,
            last_name=record.last_name,
            subscribed=record.subscribed,
            timezone=record.timezone,
            created_at=record.created_at,
            last_seen=record.last_seen,
        )
