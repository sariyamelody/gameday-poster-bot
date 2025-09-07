"""Game notification scheduler."""

from collections.abc import Callable
from datetime import datetime, timedelta

import pytz
import structlog
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from ..config import Settings
from ..models import Game, NotificationJob, NotificationStatus

logger = structlog.get_logger(__name__)


class GameScheduler:
    """Scheduler for managing game notification jobs."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the game scheduler."""
        self.settings = settings
        self.timezone = pytz.timezone(settings.scheduler_timezone)

        # Configure job store
        jobstores = {
            'default': SQLAlchemyJobStore(url=settings.database_url, tablename='apscheduler_jobs')
        }

        # Configure executors
        executors = {
            'default': AsyncIOExecutor()
        }

        # Job defaults
        job_defaults = {
            'coalesce': False,
            'max_instances': 3,
            'misfire_grace_time': 300  # 5 minutes grace period
        }

        # Initialize scheduler
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone=self.timezone
        )

        # Callback functions
        self.notification_callback: Callable[[NotificationJob], None] | None = None
        self.schedule_sync_callback: Callable[[], None] | None = None

        logger.info("Game scheduler initialized", timezone=settings.scheduler_timezone)

    async def start(self) -> None:
        """Start the scheduler."""
        try:
            self.scheduler.start()

            # Schedule daily sync job
            self._schedule_daily_sync()

            logger.info("Game scheduler started")

        except Exception as e:
            logger.error("Failed to start scheduler", error=str(e))
            raise

    async def shutdown(self) -> None:
        """Shutdown the scheduler gracefully."""
        logger.info("Shutting down game scheduler")

        try:
            self.scheduler.shutdown(wait=True)
            logger.info("Game scheduler shutdown complete")

        except Exception as e:
            logger.error("Error during scheduler shutdown", error=str(e))
            raise

    def set_notification_callback(self, callback: Callable[[NotificationJob], None]) -> None:
        """Set the callback function for sending notifications."""
        self.notification_callback = callback
        logger.debug("Notification callback set")

    def set_schedule_sync_callback(self, callback: Callable[[], None]) -> None:
        """Set the callback function for syncing schedules."""
        self.schedule_sync_callback = callback
        logger.debug("Schedule sync callback set")

    def schedule_game_notifications(self, games: list[Game]) -> int:
        """Schedule notification jobs for a list of games."""
        scheduled_count = 0

        for game in games:
            if self._should_schedule_game(game):
                try:
                    self._schedule_game_notification(game)
                    scheduled_count += 1

                except Exception as e:
                    logger.error(
                        "Failed to schedule game notification",
                        game_id=game.game_id,
                        error=str(e)
                    )

        logger.info("Scheduled game notifications", count=scheduled_count, total_games=len(games))
        return scheduled_count

    def schedule_notification_job(self, job: NotificationJob) -> bool:
        """Schedule a specific notification job."""
        try:
            # Skip if job is too far in the past
            if job.scheduled_time < datetime.utcnow() - timedelta(minutes=5):
                logger.warning(
                    "Skipping job scheduled too far in the past",
                    job_id=job.job_id,
                    scheduled_time=job.scheduled_time
                )
                return False

            # Create the job
            self.scheduler.add_job(
                self._send_notification_wrapper,
                trigger=DateTrigger(run_date=job.scheduled_time),
                args=[job],
                id=job.job_id,
                replace_existing=True,
                max_instances=1
            )

            logger.info(
                "Scheduled notification job",
                job_id=job.job_id,
                scheduled_time=job.scheduled_time,
                game_id=job.game_id
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to schedule notification job",
                job_id=job.job_id,
                error=str(e)
            )
            return False

    def cancel_notification_job(self, job_id: str) -> bool:
        """Cancel a scheduled notification job."""
        try:
            self.scheduler.remove_job(job_id)
            logger.info("Cancelled notification job", job_id=job_id)
            return True

        except Exception as e:
            logger.warning("Failed to cancel job", job_id=job_id, error=str(e))
            return False

    def get_scheduled_jobs(self) -> list[str]:
        """Get list of currently scheduled job IDs."""
        try:
            jobs = self.scheduler.get_jobs()
            job_ids = [job.id for job in jobs if job.id.startswith('mariners_game_')]

            logger.debug("Retrieved scheduled jobs", count=len(job_ids))
            return job_ids

        except Exception as e:
            logger.error("Failed to get scheduled jobs", error=str(e))
            return []

    def _should_schedule_game(self, game: Game) -> bool:
        """Check if a game should have a notification scheduled."""
        # Skip if notification already sent
        if game.notification_sent:
            return False

        # Skip if game is not a Mariners game
        if not game.is_mariners_game:
            return False

        # Skip if game is not scheduled
        if game.status.value != "scheduled":
            return False

        # Skip if game is in the past
        notification_time = game.date - timedelta(minutes=self.settings.notification_advance_minutes)
        if notification_time < datetime.utcnow():
            return False

        return True

    def _schedule_game_notification(self, game: Game) -> None:
        """Schedule a notification for a specific game."""
        # Calculate notification time (5 minutes before game start)
        notification_time = game.date - timedelta(minutes=self.settings.notification_advance_minutes)

        # Create notification job
        message = self._create_notification_message(game)
        job = NotificationJob(
            game_id=game.game_id,
            scheduled_time=notification_time,
            message=message,
            status=NotificationStatus.PENDING
        )

        # Schedule the job
        self.schedule_notification_job(job)

    def _create_notification_message(self, game: Game) -> str:
        """Create the notification message for a game."""
        opponent = game.opponent
        venue = game.venue

        # Convert game time to Pacific timezone for display
        game_time_pt = game.date.astimezone(self.timezone)
        time_str = game_time_pt.strftime("%I:%M %p %Z")

        # Determine home/away status
        location_emoji = "🏠" if game.is_mariners_home else "✈️"
        location_text = "at home" if game.is_mariners_home else "away"

        message = (
            f"🔥 <b>Mariners Game Starting Soon!</b>\n"
            f"⚾ Seattle Mariners vs {opponent}\n"
            f"🏟️ {venue}\n"
            f"📍 Playing {location_text} {location_emoji}\n"
            f"🕐 Starts in {self.settings.notification_advance_minutes} minutes ({time_str})\n"
            f"📺 <a href=\"{game.gameday_url}\">Watch Live on MLB Gameday</a>"
        )

        return message

    def _schedule_daily_sync(self) -> None:
        """Schedule the daily schedule sync job."""
        if not self.schedule_sync_callback:
            logger.warning("No schedule sync callback set, skipping daily sync scheduling")
            return

        # Schedule daily at the configured hour (6 AM PT by default)
        self.scheduler.add_job(
            self._sync_schedule_wrapper,
            trigger=CronTrigger(
                hour=self.settings.schedule_sync_hour,
                minute=0,
                timezone=self.timezone
            ),
            id='daily_schedule_sync',
            replace_existing=True,
            max_instances=1
        )

        logger.info(
            "Scheduled daily sync job",
            hour=self.settings.schedule_sync_hour,
            timezone=self.settings.scheduler_timezone
        )

    async def _send_notification_wrapper(self, job: NotificationJob) -> None:
        """Wrapper for sending notifications with error handling."""
        try:
            if self.notification_callback:
                await self.notification_callback(job)
            else:
                logger.error("No notification callback set", job_id=job.job_id)

        except Exception as e:
            logger.error(
                "Error in notification callback",
                job_id=job.job_id,
                error=str(e)
            )

    async def _sync_schedule_wrapper(self) -> None:
        """Wrapper for schedule sync with error handling."""
        try:
            if self.schedule_sync_callback:
                await self.schedule_sync_callback()
            else:
                logger.error("No schedule sync callback set")

        except Exception as e:
            logger.error("Error in schedule sync callback", error=str(e))
