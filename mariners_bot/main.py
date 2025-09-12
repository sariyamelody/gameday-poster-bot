"""Main application entry point."""

import asyncio
import signal
import sys
from datetime import datetime, timedelta

import click
import structlog
import uvloop

from .api.server import HealthServer
from .bot import TelegramBot
from .clients import MLBClient
from .config import get_settings
from .database import Repository, get_database_session
from .models import Game
from .observability import create_app_metrics, get_tracer, setup_telemetry
from .scheduler import GameScheduler

# Setup structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


class MarinersBot:
    """Main application class for the Mariners notification bot."""

    def __init__(self) -> None:
        """Initialize the bot application."""
        self.settings = get_settings()

        # Initialize OpenTelemetry observability
        setup_telemetry(self.settings)
        self.tracer = get_tracer("mariners-bot.main")
        self.metrics = create_app_metrics()

        self.db_session = get_database_session(self.settings)
        self.scheduler = GameScheduler(self.settings)
        self.telegram_bot = TelegramBot(self.settings)
        self.health_server = HealthServer()
        self.running = False

        # Setup scheduler callbacks
        self.scheduler.set_notification_callback(self.telegram_bot.send_notification)
        self.scheduler.set_schedule_sync_callback(self._sync_schedule)

        logger.info("Mariners bot initialized", version="0.1.0")

    async def start(self) -> None:
        """Start the bot application."""
        try:
            logger.info("Starting Mariners bot")

            # Initialize database
            await self.db_session.create_tables()

            # Start health check server first
            await self.health_server.start()

            # Start scheduler
            await self.scheduler.start()

            # Perform initial schedule sync
            await self._sync_schedule()

            # Start Telegram bot
            await self.telegram_bot.start_polling()

            self.running = True
            logger.info("Mariners bot started successfully")

            # Keep running until stopped
            while self.running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("Failed to start bot", error=str(e))
            raise

    async def stop(self) -> None:
        """Stop the bot application gracefully."""
        logger.info("Stopping Mariners bot")

        self.running = False

        try:
            # Stop Telegram bot
            await self.telegram_bot.stop_polling()

            # Stop scheduler
            await self.scheduler.shutdown()

            # Stop health server
            await self.health_server.stop()

            # Close database connections
            await self.db_session.close()

            logger.info("Mariners bot stopped successfully")

        except Exception as e:
            logger.error("Error during shutdown", error=str(e))

    async def _sync_schedule(self) -> None:
        """Sync the Mariners schedule from MLB API."""
        logger.info("Starting schedule sync")

        try:
            # Fetch schedule for the current season
            async with MLBClient(self.settings) as mlb_client:
                # Get remaining games this season
                games = await mlb_client.get_team_schedule(
                    start_date=datetime.now(),
                    end_date=datetime(datetime.now().year, 12, 31)
                )

            if not games:
                logger.warning("No games found in schedule sync")
                return

            # Save games to database
            saved_count = 0
            async with self.db_session.get_session() as session:
                repository = Repository(session)

                for game in games:
                    if game.is_mariners_game:
                        await repository.save_game(game)
                        saved_count += 1

            logger.info("Saved games to database", count=saved_count)

            # Schedule notifications for upcoming games
            upcoming_games = await self._get_upcoming_games()
            scheduled_count = await self.scheduler.schedule_game_notifications(upcoming_games)

            logger.info(
                "Schedule sync completed",
                total_games=len(games),
                saved_games=saved_count,
                scheduled_notifications=scheduled_count
            )

        except Exception as e:
            logger.error("Failed to sync schedule", error=str(e))
            raise

    async def _get_upcoming_games(self) -> list[Game]:
        """Get upcoming games that need notifications."""
        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)

                # Get games starting from tomorrow (to avoid today's games that might have started)
                tomorrow = datetime.now() + timedelta(days=1)
                games = await repository.get_upcoming_games(limit=50)

                # Filter for games starting tomorrow or later
                upcoming_games = [
                    game for game in games
                    if game.date >= tomorrow and not game.notification_sent
                ]

                logger.debug("Retrieved upcoming games", count=len(upcoming_games))
                return upcoming_games

        except Exception as e:
            logger.error("Failed to get upcoming games", error=str(e))
            return []


# Global bot instance
bot_instance: MarinersBot | None = None


async def main_async() -> None:
    """Async main function."""
    global bot_instance

    # Set up signal handlers for graceful shutdown
    def signal_handler(signum: int, _frame: object) -> None:
        logger.info("Received shutdown signal", signal=signum)
        if bot_instance:
            asyncio.create_task(bot_instance.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and start bot
    bot_instance = MarinersBot()

    try:
        await bot_instance.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error("Bot crashed", error=str(e))
        sys.exit(1)
    finally:
        if bot_instance:
            await bot_instance.stop()


@click.group()
def cli() -> None:
    """Seattle Mariners Gameday Telegram Bot."""
    pass


@cli.command()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--traces-stdout", is_flag=True, help="Enable OpenTelemetry traces to stdout")
@click.option("--trace-exporter", type=click.Choice(['none', 'console', 'otlp']),
              default='none', help="OpenTelemetry trace exporter to use")
def start(debug: bool, traces_stdout: bool, trace_exporter: str) -> None:
    """Start the Mariners notification bot."""
    import os

    # Configure logging level
    if debug:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    # Override OTEL settings if CLI options provided
    if traces_stdout:
        os.environ["OTEL_TRACES_TO_STDOUT"] = "true"
    if trace_exporter != 'none':
        os.environ["OTEL_TRACES_EXPORTER"] = trace_exporter

    # Use uvloop for better async performance
    uvloop.install()

    logger.info("Starting Mariners bot", debug=debug)

    # Run the bot
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error("Bot failed to start", error=str(e))
        sys.exit(1)


@cli.command()
@click.option("--days", default=7, help="Number of days to sync")
def sync_schedule(days: int) -> None:
    """Manually sync the game schedule."""

    async def sync() -> None:
        settings = get_settings()

        try:
            async with MLBClient(settings) as mlb_client:
                end_date = datetime.now() + timedelta(days=days)
                games = await mlb_client.get_team_schedule(
                    start_date=datetime.now(),
                    end_date=end_date
                )

            mariners_games = [g for g in games if g.is_mariners_game]

            click.echo(f"Found {len(mariners_games)} Mariners games in the next {days} days:")
            for game in mariners_games:
                click.echo(f"  {game}")

        except Exception as e:
            click.echo(f"Error syncing schedule: {e}")
            sys.exit(1)

    uvloop.install()
    asyncio.run(sync())


@cli.command()
@click.option("--port", type=int, help="Port to run health server on (overrides config)")
def health(port: int | None) -> None:
    """Run the health check server only."""
    import os

    from .api.server import run_health_server_standalone

    if port:
        os.environ["HEALTH_CHECK_PORT"] = str(port)

    uvloop.install()
    asyncio.run(run_health_server_standalone())


@cli.command()
def init_db() -> None:
    """Initialize the database (creates tables directly, use migrate for production)."""

    async def init() -> None:
        settings = get_settings()
        db_session = get_database_session(settings)

        try:
            await db_session.create_tables()
            click.echo("Database initialized successfully")
        except Exception as e:
            click.echo(f"Error initializing database: {e}")
            sys.exit(1)
        finally:
            await db_session.close()

    uvloop.install()
    asyncio.run(init())


@cli.command()
@click.option("--message", "-m", help="Migration message")
def migrate(message: str | None) -> None:
    """Create a new database migration."""
    import subprocess

    if not message:
        message = click.prompt("Migration message")

    try:
        result = subprocess.run([
            "uv", "run", "alembic", "revision", "--autogenerate", "-m", message
        ], capture_output=True, text=True, check=True)
        click.echo(result.stdout)
        if result.stderr:
            click.echo(result.stderr, err=True)
    except subprocess.CalledProcessError as e:
        click.echo(f"Error creating migration: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--revision", help="Target revision (default: head)")
def upgrade(revision: str | None) -> None:
    """Apply database migrations."""
    import subprocess

    revision = revision or "head"

    try:
        result = subprocess.run([
            "uv", "run", "alembic", "upgrade", revision
        ], capture_output=True, text=True, check=True)
        click.echo(result.stdout)
        if result.stderr:
            click.echo(result.stderr, err=True)
        click.echo(f"Database upgraded to {revision}")
    except subprocess.CalledProcessError as e:
        click.echo(f"Error upgrading database: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--revision", help="Target revision")
def downgrade(revision: str) -> None:
    """Downgrade database to a previous migration."""
    import subprocess

    if not revision:
        click.echo("Revision is required for downgrade", err=True)
        sys.exit(1)

    try:
        result = subprocess.run([
            "uv", "run", "alembic", "downgrade", revision
        ], capture_output=True, text=True, check=True)
        click.echo(result.stdout)
        if result.stderr:
            click.echo(result.stderr, err=True)
        click.echo(f"Database downgraded to {revision}")
    except subprocess.CalledProcessError as e:
        click.echo(f"Error downgrading database: {e}", err=True)
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    cli()


if __name__ == "__main__":
    main()
