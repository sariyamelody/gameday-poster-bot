"""Main application entry point."""

import asyncio
import signal
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .clients.bluesky_client import SalmonRunPost

import click
import structlog
import uvloop
from sqlalchemy import and_, or_, select

from .api.server import HealthServer
from .bot import TelegramBot
from .clients import MLBClient
from .config import get_settings
from .database import Repository, get_database_session
from .database.models import GameRecord
from .models import Game, Transaction
from .observability import setup_telemetry, shutdown_telemetry
from .scheduler import GameScheduler
from .scheduler.salmon_run_monitor import SalmonRunMonitor
from .scheduler.transaction_scheduler import TransactionNotificationBatcher, TransactionScheduler

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

        self.db_session = get_database_session(self.settings)
        self.scheduler = GameScheduler(self.settings)
        self.transaction_scheduler = TransactionScheduler(self.settings)
        self.transaction_batcher = TransactionNotificationBatcher(batch_window_minutes=10)
        self.telegram_bot = TelegramBot(self.settings)
        self.health_server = HealthServer()
        self.running = False

        # Setup scheduler callbacks
        self.scheduler.set_notification_callback(self.telegram_bot.send_notification)
        self.scheduler.set_schedule_sync_callback(self._sync_schedule)
        self.scheduler.set_final_score_callback(self._check_final_scores)
        self.transaction_scheduler.set_transaction_sync_callback(self._sync_transactions)

        # Salmon Run monitor (polls Bluesky between innings at home games)
        self.salmon_run_monitor = SalmonRunMonitor(
            self.settings, on_result=self._post_salmon_run_result
        )

        # Play-by-play callbacks (only wired when channel is configured)
        if self.settings.playbyplay_channel_id and self.settings.playbyplay_group_id:
            self.scheduler.set_playbyplay_callback(self._poll_playbyplay)
            self.scheduler.set_playbyplay_cleanup_callback(self._cleanup_playbyplay_data)

        logger.info("Mariners bot initialized", version="0.1.0")

    def _run_migrations(self) -> None:
        """Run Alembic migrations to bring the database schema up to date.

        Handles three cases:
        - Fresh install: create_tables() creates all tables, then we stamp at
          head (no migrations to run).
        - Existing DB with no migration history: compare_metadata detects any
          schema drift; if current we stamp head, otherwise we stamp at the
          parent of head so upgrade applies only the missing migration(s).
        - Existing DB with migration history: run upgrade head normally.
        """
        from sqlalchemy import inspect

        from alembic import command
        from alembic.autogenerate import compare_metadata
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from mariners_bot.database.models import Base

        logger.info("Running database migrations")
        alembic_cfg = Config("alembic.ini")

        with self.db_session.sync_engine.connect() as conn:
            if "alembic_version" not in inspect(conn).get_table_names():
                if "games" not in inspect(conn).get_table_names():
                    # Fresh install — schema already current from create_tables()
                    command.stamp(alembic_cfg, "head")
                else:
                    # Existing install without migration history — detect schema state
                    mc = MigrationContext.configure(conn, opts={"compare_type": False})
                    diff = compare_metadata(mc, Base.metadata)
                    if not diff:
                        # Schema already matches models — nothing to migrate
                        command.stamp(alembic_cfg, "head")
                    else:
                        # Schema is behind — stamp at the parent of head so
                        # upgrade head applies the missing migration(s)
                        script = ScriptDirectory.from_config(alembic_cfg)
                        current_head = script.get_current_head()
                        if current_head is None:
                            raise RuntimeError("No Alembic head revision found")
                        head_rev = script.get_revision(current_head)
                        command.stamp(alembic_cfg, str(head_rev.down_revision))

        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations complete")

    async def start(self) -> None:
        """Start the bot application."""
        try:
            logger.info("Starting Mariners bot")

            # Initialize base schema (safe on existing databases)
            await self.db_session.create_tables()

            # Run migrations for incremental schema changes
            self._run_migrations()

            # Start health check server first
            await self.health_server.start()

            # Start scheduler
            await self.scheduler.start()

            # Start transaction scheduler
            await self.transaction_scheduler.start()

            # Perform initial schedule sync
            await self._sync_schedule()

            # Perform initial transaction sync
            await self._sync_transactions()

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

        self.salmon_run_monitor.stop()

        try:
            # Stop Telegram bot
            await self.telegram_bot.stop_polling()

            # Stop scheduler
            await self.scheduler.shutdown()

            # Stop transaction scheduler
            await self.transaction_scheduler.shutdown()

            # Stop health server
            await self.health_server.stop()

            # Close database connections
            await self.db_session.close()

            # Flush and shut down telemetry last so any shutdown-path spans are exported
            shutdown_telemetry()

            logger.info("Mariners bot stopped successfully")

        except Exception as e:
            logger.error("Error during shutdown", error=str(e))

    async def _sync_schedule(self) -> None:
        """Sync the Mariners schedule from MLB API."""
        logger.info("Starting schedule sync")

        try:
            async with MLBClient(self.settings) as mlb_client:
                current_year = datetime.now().year
                current_date = datetime.now()

                all_games = []

                # Get remaining games from current season (including postseason)
                current_season_games = await mlb_client.get_team_schedule(
                    start_date=current_date,
                    end_date=datetime(current_year, 12, 31),
                    season=current_year
                )
                all_games.extend(current_season_games)
                logger.info("Fetched current season games",
                           season=current_year,
                           count=len(current_season_games))

                # If we're in the off-season (after September), also get next season's games
                if current_date.month >= 10:  # October or later
                    next_year = current_year + 1
                    next_season_games = await mlb_client.get_team_schedule(
                        start_date=datetime(next_year, 1, 1),
                        end_date=datetime(next_year, 12, 31),
                        season=next_year
                    )
                    all_games.extend(next_season_games)
                    logger.info("Fetched next season games",
                               season=next_year,
                               count=len(next_season_games))

            if not all_games:
                logger.warning("No games found in schedule sync")
                return

            # Save games to database
            saved_count = 0
            async with self.db_session.get_session() as session:
                repository = Repository(session)

                for game in all_games:
                    if game.is_mariners_game:
                        await repository.save_game(game)
                        saved_count += 1

            logger.info("Saved games to database", count=saved_count)

            # Schedule notifications for upcoming games
            upcoming_games = await self._get_upcoming_games()
            scheduled_count = await self.scheduler.schedule_game_notifications(upcoming_games)

            logger.info(
                "Schedule sync completed",
                total_games=len(all_games),
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

    async def _sync_transactions(self) -> None:
        """Sync the latest Mariners transactions from MLB API."""
        logger.info("Starting transaction sync")

        try:
            # Fetch transactions for the last 7 days (to catch any recent updates)
            start_date = (datetime.now() - timedelta(days=7)).date()
            end_date = datetime.now().date()

            async with MLBClient(self.settings) as mlb_client:
                transactions = await mlb_client.get_mariners_transactions(
                    start_date=start_date,
                    end_date=end_date
                )

            if not transactions:
                logger.debug("No transactions found in sync")
                return

            # Save transactions to database and identify new ones
            new_transactions = []
            async with self.db_session.get_session() as session:
                repository = Repository(session)

                for transaction in transactions:
                    if transaction.is_mariners_transaction:
                        # Check if this is a new transaction
                        existing = await self._is_transaction_existing(repository, transaction.transaction_id)
                        if not existing:
                            new_transactions.append(transaction)

                        await repository.save_transaction(transaction)

            if new_transactions:
                logger.info("Found new transactions", count=len(new_transactions))
                await self._process_new_transactions(new_transactions)
            else:
                logger.debug("No new transactions to process")

            # Process any pending batched notifications
            await self._process_pending_transaction_batches()

            logger.info(
                "Transaction sync completed",
                total_transactions=len(transactions),
                new_transactions=len(new_transactions)
            )

        except Exception as e:
            logger.error("Failed to sync transactions", error=str(e))
            raise

    async def _check_final_scores(self) -> None:
        """Check for completed games and send final score notifications."""
        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)
                games = await repository.get_games_needing_final_score()

            if not games:
                return

            logger.debug("Checking final scores", game_count=len(games))

            async with MLBClient(self.settings) as mlb_client:
                for game in games:
                    try:
                        score_data = await mlb_client.get_game_score(game.game_id)

                        if not score_data or not score_data["is_final"]:
                            continue

                        message = self._create_final_score_message(game, score_data)

                        # Send to channel if configured
                        if self.settings.telegram_chat_id:
                            await self.telegram_bot._send_message_with_retry(
                                chat_id=self.settings.telegram_chat_id,
                                message=message
                            )

                        # Send to all subscribed users
                        async with self.db_session.get_session() as session:
                            repository = Repository(session)
                            users = await repository.get_subscribed_users()
                            await repository.mark_game_final_score_sent(game.game_id)
                            await session.commit()

                        for user in users:
                            if str(user.chat_id) != self.settings.telegram_chat_id:
                                await self.telegram_bot._send_message_with_retry(
                                    chat_id=str(user.chat_id),
                                    message=message
                                )

                        logger.info("Sent final score notification", game_id=game.game_id)

                    except Exception as e:
                        logger.error("Failed to process final score for game",
                                     game_id=game.game_id, error=str(e))

        except Exception as e:
            logger.error("Failed to check final scores", error=str(e))

    def _create_final_score_message(self, game: "Game", score_data: dict[str, object]) -> str:
        """Create a final score message with spoiler tags."""
        is_home = game.is_mariners_home
        mariners_score = score_data["home_score"] if is_home else score_data["away_score"]
        opponent_score = score_data["away_score"] if is_home else score_data["home_score"]
        mariners_won = score_data["home_winner"] if is_home else score_data["away_winner"]

        result_emoji = "✅" if mariners_won else "❌"
        result_text = "WIN" if mariners_won else "loss"

        innings = score_data.get("innings")
        innings_note = f" ({innings} innings)" if innings and isinstance(innings, int) and innings != 9 else ""

        return (
            f"⚾ <b>Final Score — Mariners vs {game.opponent}</b>\n\n"
            f"<tg-spoiler>{result_emoji} Mariners {result_text}{innings_note}: "
            f"{mariners_score}–{opponent_score}</tg-spoiler>\n\n"
            f"📊 <a href=\"{game.baseball_savant_url}\">Full Game on Baseball Savant</a>"
        )

    async def _is_transaction_existing(self, repository: Repository, transaction_id: int) -> bool:
        """Check if a transaction already exists in the database."""
        try:
            # Check if transaction exists in database (regardless of notification status)
            return await repository.transaction_exists(transaction_id)
        except Exception:
            # If we can't check, assume it's new to be safe
            return False

    async def _process_new_transactions(self, transactions: list[Transaction]) -> None:
        """Process new transactions and send notifications."""
        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)

                # Send notifications to channel (all transactions, no batching for channel)
                if self.settings.telegram_chat_id:
                    await self._send_channel_transaction_notifications(transactions)

                # Process individual user notifications with batching
                for transaction in transactions:
                    users_preferences = await repository.get_users_for_transaction_notification(transaction)

                    for user, _preferences in users_preferences:
                        await self._handle_user_transaction_notification(user.chat_id, transaction, repository)

        except Exception as e:
            logger.error("Failed to process new transactions", error=str(e))

    async def _send_channel_transaction_notifications(self, transactions: list[Transaction]) -> None:
        """Send transaction notifications to the main channel."""
        try:
            if not self.settings.telegram_chat_id:
                return

            # Sort transactions by priority and date
            transactions.sort(key=lambda t: (t.transaction_date, t.transaction_id))

            # Split into optimal batches
            batches = TransactionNotificationBatcher.split_transactions_for_batching(transactions)

            for batch in batches:
                message = Transaction.format_batch_notification_message(batch)
                if message:
                    success = await self.telegram_bot._send_message_with_retry(
                        chat_id=self.settings.telegram_chat_id,
                        message=message
                    )

                    if success:
                        logger.info("Sent channel transaction notification", batch_size=len(batch))
                    else:
                        logger.error("Failed to send channel transaction notification", batch_size=len(batch))

        except Exception as e:
            logger.error("Failed to send channel transaction notifications", error=str(e))

    async def _handle_user_transaction_notification(self, chat_id: int, transaction: Transaction, repository: Repository) -> None:
        """Handle transaction notification for a specific user with batching."""
        try:
            # Check if we should batch this notification
            should_batch = self.transaction_batcher.should_batch_notification(chat_id, transaction)

            if should_batch:
                # Add to batch
                self.transaction_batcher.add_transaction_to_batch(chat_id, transaction)
                logger.debug("Added transaction to user batch", chat_id=chat_id, transaction_id=transaction.transaction_id)
            else:
                # Send immediately (possibly with any pending batch)
                pending_batch = self.transaction_batcher.get_and_clear_batch(chat_id)
                all_transactions = pending_batch + [transaction]

                message = Transaction.format_batch_notification_message(all_transactions)
                if message:
                    success = await self.telegram_bot._send_message_with_retry(
                        chat_id=str(chat_id),
                        message=message
                    )

                    if success:
                        self.transaction_batcher.mark_notification_sent(chat_id)
                        # Mark all transactions as notified
                        for t in all_transactions:
                            await repository.mark_transaction_notified(t.transaction_id)

                        logger.info("Sent user transaction notification",
                                  chat_id=chat_id, batch_size=len(all_transactions))
                    else:
                        logger.error("Failed to send user transaction notification", chat_id=chat_id)

        except Exception as e:
            logger.error("Failed to handle user transaction notification",
                        chat_id=chat_id, transaction_id=transaction.transaction_id, error=str(e))

    # -------------------------------------------------------------------------
    # Play-by-play polling
    # -------------------------------------------------------------------------

    # Event emoji for play descriptions
    _PLAY_EMOJIS: dict[str, str] = {
        "Home Run": "🚨",
        "Triple": "🟣",
        "Double": "🔵",
        "Single": "⚪",
        "Walk": "🟡",
        "Intent Walk": "🟡",
        "Hit By Pitch": "💥",
        "Strikeout": "❌",
        "Strikeout - DP": "❌",
        "Groundout": "⬜",
        "Grounded Into Double Play": "🔁",
        "Double Play": "🔁",
        "Triple Play": "🔁",
        "Flyout": "⬜",
        "Lineout": "⬜",
        "Pop Out": "⬜",
        "Bunt Groundout": "⬜",
        "Bunt Pop Out": "⬜",
        "Sac Fly": "⬜",
        "Sac Fly Double Play": "🔁",
        "Sac Bunt": "⬜",
        "Sac Bunt Double Play": "🔁",
        "Field Error": "🔴",
        "Fielding Error": "🔴",
        "Fielder's Choice": "⬜",
        "Fielder's Choice Out": "⬜",
        "Wild Pitch": "🌀",
        "Passed Ball": "🌀",
        "Balk": "🌀",
        "Stolen Base 2B": "💨",
        "Stolen Base 3B": "💨",
        "Stolen Base Home": "💨",
        "Caught Stealing 2B": "🛑",
        "Caught Stealing 3B": "🛑",
        "Caught Stealing Home": "🛑",
        "Pickoff 1B": "🛑",
        "Pickoff 2B": "🛑",
        "Pickoff 3B": "🛑",
        "Runner Out": "🛑",
        "Manager Challenge": "📋",
        "Umpire Review": "📋",
    }

    def _ordinal(self, n: int) -> str:
        """Return ordinal string for inning number (1st, 2nd, 3rd…)."""
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")
        return f"{n}{suffix}"

    def _score_line(self, linescore: dict[str, Any], away_abbr: str, home_abbr: str) -> str:
        """Format a compact score line: e.g. 'SEA 2 · HOU 1'."""
        teams = linescore.get("teams", {})
        away_runs = teams.get("away", {}).get("runs", 0)
        home_runs = teams.get("home", {}).get("runs", 0)
        return f"{away_abbr} {away_runs} · {home_abbr} {home_runs}"

    def _format_inning_header(
        self,
        inning: int,
        half: str,
        linescore: dict[str, Any],
        away_abbr: str,
        home_abbr: str,
    ) -> str:
        """Build the channel/group inning header message."""
        half_label = "Top" if half == "top" else "Bottom"
        ordinal = self._ordinal(inning)

        score = self._score_line(linescore, away_abbr, home_abbr)

        # Current pitcher from linescore defense
        defense = linescore.get("defense", {})
        pitcher_name = defense.get("pitcher", {}).get("fullName", "")
        pitcher_line = f"{pitcher_name} pitching" if pitcher_name else ""

        lines = [f"⚾ {half_label} of the {ordinal} Inning", score]
        if pitcher_line:
            lines.append(pitcher_line)

        return "\n".join(lines)

    def _format_play(self, play: dict[str, Any]) -> str:
        """Format a single completed play for the group thread."""
        result = play.get("result", {})
        event = result.get("event", "")
        description = result.get("description", "")
        is_scoring = play.get("about", {}).get("isScoringPlay", False)
        away_score = result.get("awayScore")
        home_score = result.get("homeScore")

        emoji = self._PLAY_EMOJIS.get(event, "⚾")
        text = f"{emoji} {description}"

        review = play.get("reviewDetails")
        if review:
            if review.get("isOverturned"):
                text += "\n✅ <b>Call overturned</b>"
            else:
                text += "\n❌ <b>Call upheld</b>"

        for ev in play.get("playEvents", []):
            pitch_review = ev.get("reviewDetails")
            if pitch_review and ev.get("isPitch"):
                call_desc = ev.get("details", {}).get("call", {}).get("description", "pitch")
                challenger = pitch_review.get("player", {}).get("fullName", "")
                challenger_prefix = f"{challenger} challenges" if challenger else "Challenge"
                if pitch_review.get("isOverturned"):
                    text += f"\n📋 {challenger_prefix} ({call_desc.lower()}) — ✅ <b>Call overturned</b>"
                else:
                    text += f"\n📋 {challenger_prefix} ({call_desc.lower()}) — ❌ <b>Call upheld</b>"
                break

        if is_scoring and away_score is not None and home_score is not None:
            text += f"\n<b>{away_score}–{home_score}</b>"

        return text

    def _format_inning_footer(
        self,
        inning: int,
        half: str,
        linescore: dict[str, Any],
        away_abbr: str,
        home_abbr: str,
    ) -> str:
        """Build the end-of-inning summary posted in the group thread."""
        half_label = "Top" if half == "top" else "Bottom"
        ordinal = self._ordinal(inning)

        # Per-inning stats from linescore.innings
        innings_data = linescore.get("innings", [])
        inning_entry: dict[str, Any] = next((i for i in innings_data if i.get("num") == inning), {})
        half_key = "away" if half == "top" else "home"
        half_data = inning_entry.get(half_key, {})
        runs = half_data.get("runs", 0)
        hits = half_data.get("hits", 0)
        errors = half_data.get("errors", 0)

        score = self._score_line(linescore, away_abbr, home_abbr)

        return (
            f"— End of {half_label} {ordinal} —\n"
            f"{runs}R · {hits}H · {errors}E\n\n"
            f"<b>{score}</b>"
        )

    async def _poll_playbyplay(self) -> None:
        """Scheduled callback: poll live game feeds and post play-by-play updates."""
        if not (self.settings.playbyplay_channel_id and self.settings.playbyplay_group_id):
            return

        try:
            # Games that are in-progress or notified but not yet final, within a 6-hour window.
            # Include games whose start time has passed even if the pre-game notification was
            # missed (e.g. bot was down), so PBP still fires for in-progress games.
            now = datetime.now(UTC)
            cutoff = now - timedelta(hours=6)
            async with self.db_session.get_session() as session:
                result = await session.execute(
                    select(GameRecord).where(
                        and_(
                            or_(
                                GameRecord.notification_sent == True,  # noqa: E712
                                GameRecord.date <= now,
                            ),
                            GameRecord.final_score_sent == False,  # noqa: E712
                            GameRecord.date >= cutoff,
                        )
                    )
                )
                candidate_records = list(result.scalars())

            if not candidate_records:
                return

            async with MLBClient(self.settings) as mlb_client:
                for record in candidate_records:
                    game_id = str(record.game_id)
                    game_pk = int(game_id)
                    try:
                        await self._process_game_playbyplay(mlb_client, game_id, game_pk)
                    except Exception as e:
                        logger.error("Failed to process play-by-play for game", game_id=game_id, error=str(e))

        except Exception as e:
            logger.error("Failed to poll play-by-play", error=str(e))

    async def _process_game_playbyplay(
        self,
        mlb_client: MLBClient,
        game_id: str,
        game_pk: int,
    ) -> None:
        """Process one game's live feed: ensure session exists, post new plays, edit corrections."""
        # Ensure session exists
        async with self.db_session.get_session() as session:
            from .database import Repository as Repo
            repo = Repo(session)
            pbp_session = await repo.get_or_create_playbyplay_session(game_id, game_pk)
            if not pbp_session.active:
                return
            last_play_index = pbp_session.last_play_index
            await session.commit()

        feed = await mlb_client.get_live_game_feed(game_pk)
        if not feed:
            return

        game_state = feed.get("gameData", {}).get("status", {}).get("abstractGameState", "")
        if game_state == "Preview":
            return

        all_plays: list[dict[str, Any]] = feed.get("liveData", {}).get("plays", {}).get("allPlays", [])
        linescore: dict[str, Any] = feed.get("liveData", {}).get("linescore", {})
        game_data = feed.get("gameData", {})
        teams = game_data.get("teams", {})
        away_abbr = teams.get("away", {}).get("abbreviation", "AWY")
        home_abbr = teams.get("home", {}).get("abbreviation", "HME")
        is_home_game = teams.get("home", {}).get("id") == self.settings.mariners_team_id

        completed_plays = [p for p in all_plays if p.get("about", {}).get("isComplete", False)]
        new_plays = sorted(
            [p for p in completed_plays if p.get("about", {}).get("atBatIndex", -1) > last_play_index],
            key=lambda p: p["about"]["atBatIndex"],
        )
        old_plays = [p for p in completed_plays if p.get("about", {}).get("atBatIndex", -1) <= last_play_index]
        # Only check recent plays for scorer corrections
        recent_old = sorted(old_plays, key=lambda p: p["about"]["atBatIndex"])[-10:]

        committed_index = last_play_index
        if new_plays:
            committed_index = await self._post_new_plays(
                game_id, new_plays, linescore, away_abbr, home_abbr, last_play_index, is_home_game
            )

        # Checkpoint progress before corrections check so a failure there doesn't
        # cause already-posted plays to be retried on the next poll.
        now = datetime.now(UTC)
        async with self.db_session.get_session() as session:
            from .database import Repository as Repo
            repo = Repo(session)
            await repo.update_playbyplay_session(
                game_id=game_id,
                last_play_index=committed_index,
                last_poll_at=now,
            )
            if game_state == "Final":
                await repo.deactivate_playbyplay_session(game_id=game_id, finished_at=now)
                logger.info("Play-by-play session complete", game_id=game_id)
            await session.commit()

        await self._check_updated_plays(game_id, recent_old)

    async def _post_new_plays(
        self,
        game_id: str,
        new_plays: list[dict[str, Any]],
        linescore: dict[str, Any],
        away_abbr: str,
        home_abbr: str,
        last_play_index: int,
        is_home_game: bool = False,
    ) -> int:
        """For each new play: open a new inning post if needed, then post the play.

        Returns the highest at_bat_index that was successfully committed to the DB,
        or last_play_index if no plays were committed.
        """
        async with self.db_session.get_session() as session:
            from .database import Repository as Repo
            repo = Repo(session)
            current_post = await repo.get_current_inning_post(game_id)

        committed_index = last_play_index

        for play in new_plays:
            about = play.get("about", {})
            play_inning = about.get("inning", 0)
            play_half = about.get("halfInning", "top")
            at_bat_index = about.get("atBatIndex", -1)

            try:
                # Open a new inning post when the inning or half changes
                if (
                    current_post is None
                    or current_post.inning != play_inning
                    or current_post.half != play_half
                ):
                    prev_post = current_post

                    # Post end-of-inning footer for the previous inning
                    if prev_post is not None and prev_post.group_message_id is not None:
                        footer_text = self._format_inning_footer(
                            prev_post.inning, prev_post.half, linescore, away_abbr, home_abbr
                        )
                        footer_msg_id = await self.telegram_bot.post_inning_footer(
                            group_message_id=prev_post.group_message_id,
                            text=footer_text,
                        )
                        if footer_msg_id:
                            async with self.db_session.get_session() as session:
                                from .database import Repository as Repo
                                repo = Repo(session)
                                await repo.update_inning_post_footer_msg_id(prev_post.id, footer_msg_id)
                                await session.commit()
                            prev_post.footer_message_id = footer_msg_id

                        # Inning ended — open the Salmon Run polling window.
                        self.salmon_run_monitor.on_inning_end(game_id, is_home_game)

                    # Post the new inning header to the channel (and wait for group forward)
                    header_text = self._format_inning_header(play_inning, play_half, linescore, away_abbr, home_abbr)
                    channel_msg_id, group_msg_id = await self.telegram_bot.post_inning_header(
                        header_text=header_text,
                    )

                    async with self.db_session.get_session() as session:
                        from .database import Repository as Repo
                        repo = Repo(session)
                        new_post = await repo.create_inning_post(
                            game_id=game_id,
                            inning=play_inning,
                            half=play_half,
                            channel_message_id=channel_msg_id,
                            group_message_id=group_msg_id,
                        )

                        # If prev inning's footer exists and we now have a channel URL,
                        # edit it to append the next-inning link.
                        if (
                            prev_post is not None
                            and prev_post.footer_message_id is not None
                            and prev_post.group_message_id is not None
                            and channel_msg_id is not None
                        ):
                            next_url = self.telegram_bot._make_channel_post_url(channel_msg_id)
                            if next_url:
                                next_half_label = "Top" if play_half == "top" else "Bottom"
                                next_ordinal = self._ordinal(play_inning)
                                footer_text = self._format_inning_footer(
                                    prev_post.inning, prev_post.half, linescore, away_abbr, home_abbr
                                )
                                updated_footer = (
                                    f"{footer_text}\n"
                                    f'⬇️ <a href="{next_url}">{next_half_label} of the {next_ordinal} →</a>'
                                )
                                await self.telegram_bot.update_inning_footer_text(
                                    footer_message_id=prev_post.footer_message_id,
                                    new_text=updated_footer,
                                )

                        await session.commit()

                    current_post = new_post

                    # New inning started — schedule polling to stop in 2 minutes.
                    self.salmon_run_monitor.on_inning_start()

                # Post the play to the group thread
                if current_post is not None and current_post.group_message_id is not None:
                    play_text = self._format_play(play)
                    group_play_msg_id = await self.telegram_bot.post_play(
                        group_message_id=current_post.group_message_id,
                        text=play_text,
                    )
                    if group_play_msg_id:
                        result_data = play.get("result", {})
                        async with self.db_session.get_session() as session:
                            from .database import Repository as Repo
                            repo = Repo(session)
                            await repo.save_play_message(
                                game_id=game_id,
                                at_bat_index=at_bat_index,
                                group_message_id=group_play_msg_id,
                                description=result_data.get("description", ""),
                                event=result_data.get("event", ""),
                            )
                            await session.commit()

                committed_index = at_bat_index

            except Exception as e:
                logger.error(
                    "Failed to process play, skipping",
                    game_id=game_id, at_bat_index=at_bat_index, error=str(e),
                )

        return committed_index

    async def _check_updated_plays(
        self, game_id: str, plays_from_feed: list[dict[str, Any]]
    ) -> None:
        """Edit any already-posted play messages that the official scorer has corrected."""
        if not plays_from_feed:
            return

        async with self.db_session.get_session() as session:
            from .database import Repository as Repo
            repo = Repo(session)

            for play in plays_from_feed:
                about = play.get("about", {})
                at_bat_index = about.get("atBatIndex", -1)
                result = play.get("result", {})
                new_desc = result.get("description", "")
                new_event = result.get("event", "")

                existing = await repo.get_play_message(game_id, at_bat_index)
                if not existing:
                    continue

                if existing.last_description != new_desc or existing.last_event != new_event:
                    new_text = self._format_play(play)
                    await self.telegram_bot.edit_play(
                        group_message_id=existing.group_message_id,
                        new_text=new_text,
                    )
                    await repo.update_play_message(existing.id, new_desc, new_event)
                    logger.info(
                        "Edited corrected play",
                        game_id=game_id,
                        at_bat_index=at_bat_index,
                        old_event=existing.last_event,
                        new_event=new_event,
                    )

            await session.commit()

    async def _post_salmon_run_result(self, post: "SalmonRunPost") -> None:
        """Send a Salmon Run result to the main channel and the PBP channel."""
        credit = f'<a href="{post.web_url}">via {post.author_display_name} on Bluesky</a>'
        message = f"🐟 <b>Salmon Run</b>\n{post.text}\n\n{credit}"
        destinations: list[str] = []
        if self.settings.telegram_chat_id:
            destinations.append(self.settings.telegram_chat_id)
        if (
            self.settings.playbyplay_channel_id
            and self.settings.playbyplay_channel_id not in destinations
        ):
            destinations.append(self.settings.playbyplay_channel_id)

        for chat_id in destinations:
            if post.thumbnail_url:
                await self.telegram_bot.send_photo_to_chat(chat_id, post.thumbnail_url, message)
            else:
                await self.telegram_bot.send_to_chat(chat_id, message)

    async def _cleanup_playbyplay_data(self) -> None:
        """Delete play-by-play data for finished sessions past the retention window."""
        try:
            async with self.db_session.get_session() as session:
                repo = Repository(session)
                deleted = await repo.cleanup_playbyplay_data(self.settings.playbyplay_retention_hours)
                await session.commit()

            if deleted:
                logger.info("Play-by-play cleanup complete", sessions_deleted=deleted)

        except Exception as e:
            logger.error("Failed to cleanup play-by-play data", error=str(e))

    async def _process_pending_transaction_batches(self) -> None:
        """Process any pending transaction batches that should be sent."""
        try:
            users_to_notify = self.transaction_batcher.get_users_with_pending_batches()

            if not users_to_notify:
                return

            async with self.db_session.get_session() as session:
                repository = Repository(session)

                for chat_id in users_to_notify:
                    pending_transactions = self.transaction_batcher.get_and_clear_batch(chat_id)

                    if pending_transactions:
                        message = Transaction.format_batch_notification_message(pending_transactions)
                        if message:
                            success = await self.telegram_bot._send_message_with_retry(
                                chat_id=str(chat_id),
                                message=message
                            )

                            if success:
                                self.transaction_batcher.mark_notification_sent(chat_id)
                                # Mark all transactions as notified
                                for transaction in pending_transactions:
                                    await repository.mark_transaction_notified(transaction.transaction_id)

                                logger.info("Sent pending transaction batch",
                                          chat_id=chat_id, batch_size=len(pending_transactions))
                            else:
                                logger.error("Failed to send pending transaction batch", chat_id=chat_id)

        except Exception as e:
            logger.error("Failed to process pending transaction batches", error=str(e))



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
@click.option("--traces-stdout", is_flag=True, help="Enable OpenTelemetry traces to stdout (alias for --trace-exporter=console)")
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
        os.environ["OTEL_TRACES_EXPORTER"] = "console"
    elif trace_exporter != 'none':
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
