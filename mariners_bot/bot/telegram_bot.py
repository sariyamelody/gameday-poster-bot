"""Telegram bot implementation."""

import asyncio

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config import Settings
from ..database import Repository, get_database_session
from ..models import NotificationJob, User

logger = structlog.get_logger(__name__)


class TelegramBot:
    """Telegram bot for sending game notifications."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the Telegram bot."""
        self.settings = settings
        self.bot_token = settings.telegram_bot_token
        self.default_chat_id = settings.telegram_chat_id

        # Create bot application
        self.application = Application.builder().token(self.bot_token).build()
        self.bot = self.application.bot

        # Setup command handlers
        self._setup_handlers()

        # Database session
        self.db_session = get_database_session(settings)

        logger.info("Telegram bot initialized")

    async def start_polling(self) -> None:
        """Start the bot with polling."""
        try:
            await self.application.initialize()
            await self.application.start()

            logger.info("Telegram bot started with polling")

            # Start polling
            if self.application.updater:
                await self.application.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES
                )

        except Exception as e:
            logger.error("Failed to start bot polling", error=str(e))
            raise

    async def stop_polling(self) -> None:
        """Stop the bot polling."""
        try:
            if self.application.updater:
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

            logger.info("Telegram bot stopped")

        except Exception as e:
            logger.error("Error stopping bot", error=str(e))

    async def send_notification(self, job: NotificationJob) -> bool:
        """Send a notification message."""
        try:
            # Determine chat ID
            chat_id = job.chat_id or self.default_chat_id
            if not chat_id:
                logger.error("No chat ID available for notification", job_id=job.job_id)
                return False

            # Send message with retry logic
            success = await self._send_message_with_retry(
                chat_id=chat_id,
                message=job.message,
                max_retries=3
            )

            if success:
                job.mark_sent()
                logger.info("Notification sent successfully", job_id=job.job_id, chat_id=chat_id)
            else:
                job.mark_failed("Failed to send after retries")
                logger.error("Failed to send notification", job_id=job.job_id)

            # Save job status to database
            await self._save_notification_job(job)

            return success

        except Exception as e:
            logger.error("Error sending notification", job_id=job.job_id, error=str(e))
            job.mark_failed(str(e))
            await self._save_notification_job(job)
            return False

    async def send_message_to_all_subscribers(self, message: str) -> int:
        """Send a message to all subscribed users."""
        sent_count = 0

        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)
                users = await repository.get_subscribed_users()

            for user in users:
                try:
                    success = await self._send_message_with_retry(
                        chat_id=str(user.chat_id),
                        message=message
                    )

                    if success:
                        sent_count += 1

                except Exception as e:
                    logger.warning(
                        "Failed to send message to user",
                        chat_id=user.chat_id,
                        error=str(e)
                    )

            logger.info("Broadcast message sent", sent_count=sent_count, total_users=len(users))
            return sent_count

        except Exception as e:
            logger.error("Error broadcasting message", error=str(e))
            return sent_count

    def _setup_handlers(self) -> None:
        """Setup command and message handlers."""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self._handle_start))
        self.application.add_handler(CommandHandler("help", self._handle_help))
        self.application.add_handler(CommandHandler("status", self._handle_status))
        self.application.add_handler(CommandHandler("subscribe", self._handle_subscribe))
        self.application.add_handler(CommandHandler("unsubscribe", self._handle_unsubscribe))
        self.application.add_handler(CommandHandler("next_game", self._handle_next_game))
        self.application.add_handler(CommandHandler("nextgame", self._handle_next_game))
        self.application.add_handler(CommandHandler("transactions", self._handle_transactions))
        self.application.add_handler(CommandHandler("transaction_settings", self._handle_transaction_settings))
        self.application.add_handler(CommandHandler("toggle_trades", self._handle_toggle_trades))
        self.application.add_handler(CommandHandler("toggle_signings", self._handle_toggle_signings))
        self.application.add_handler(CommandHandler("toggle_injuries", self._handle_toggle_injuries))
        self.application.add_handler(CommandHandler("toggle_recalls", self._handle_toggle_recalls))
        self.application.add_handler(CommandHandler("toggle_major_only", self._handle_toggle_major_only))

        # Message handler for regular text - only respond in private chats
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, self._handle_message)
        )

        logger.debug("Bot handlers configured")

    async def _handle_start(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.effective_user or not update.effective_chat:
            return

        try:
            # Create/update user record
            user = User(
                chat_id=update.effective_chat.id,
                username=update.effective_user.username,
                first_name=update.effective_user.first_name,
                last_name=update.effective_user.last_name,
            )

            await self._save_user(user)

            welcome_message = (
                f"⚾ Welcome to the Seattle Mariners Gameday Bot, {user.display_name}!\n\n"
                f"I'll notify you 5 minutes before each Mariners game starts with a direct link "
                f"to MLB Gameday.\n\n"
                f"Commands:\n"
                f"• /help - Show this help message\n"
                f"• /status - Check your subscription status\n"
                f"• /subscribe - Subscribe to notifications\n"
                f"• /unsubscribe - Unsubscribe from notifications\n"
                f"• /nextgame or /next_game - Get info about the next game\n\n"
                f"Go Mariners! 🌊"
            )

            if update.message:
                await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)

            logger.info("User started bot", chat_id=update.effective_chat.id, username=user.username)

        except Exception as e:
            logger.error("Error handling start command", error=str(e))
            if update.message:
                await update.message.reply_text("Sorry, there was an error processing your request.")

    async def _handle_help(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        help_message = (
            "⚾ <b>Seattle Mariners Gameday Bot</b>\n\n"
            "I automatically notify you 5 minutes before each Mariners game starts and "
            "keep you updated on all Mariners transactions!\n\n"
            "<b>Game Commands:</b>\n"
            "• /start - Start using the bot\n"
            "• /help - Show this help message\n"
            "• /status - Check your subscription status\n"
            "• /subscribe - Subscribe to notifications\n"
            "• /unsubscribe - Unsubscribe from notifications\n"
            "• /nextgame or /next_game - Get info about the next upcoming game\n\n"
            "<b>Transaction Commands:</b>\n"
            "• /transactions - View recent Mariners transactions\n"
            "• /transaction_settings - View/manage transaction notification preferences\n"
            "• /toggle_trades - Toggle trade notifications\n"
            "• /toggle_signings - Toggle free agent signing notifications\n"
            "• /toggle_injuries - Toggle injury list notifications\n"
            "• /toggle_recalls - Toggle player recall/option notifications\n"
            "• /toggle_major_only - Toggle major league only filter\n\n"
            "<b>Features:</b>\n"
            "• 🔔 Automatic notifications 5 minutes before games\n"
            "• 📰 Real-time Mariners transaction alerts\n"
            "• 🔗 Direct links to MLB Gameday\n"
            "• 🏟️ Game details (opponent, venue, time)\n"
            "• ⚙️ Customizable transaction notifications\n"
            "• 🌍 Timezone-aware (Pacific Time)\n\n"
            "Go Mariners! 🌊"
        )

        if update.message:
            await update.message.reply_text(help_message, parse_mode=ParseMode.HTML)

    async def _handle_status(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not update.effective_chat:
            return

        try:
            async with self.db_session.get_session() as session:
                # Check if user exists - simplified query
                from sqlalchemy import select

                from ..database.models import UserRecord

                result = await session.execute(
                    select(UserRecord).where(UserRecord.chat_id == update.effective_chat.id)
                )
                user_record = result.scalar_one_or_none()

            if user_record and user_record.subscribed:
                status_message = "✅ You are <b>subscribed</b> to Mariners game notifications!"
            elif user_record:
                status_message = "❌ You are <b>not subscribed</b> to notifications. Use /subscribe to enable them."
            else:
                status_message = "❓ You haven't started the bot yet. Use /start to begin!"

            if update.message:
                await update.message.reply_text(status_message, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error("Error checking user status", error=str(e))
            if update.message:
                await update.message.reply_text("Sorry, I couldn't check your status right now.")

    async def _handle_subscribe(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /subscribe command."""
        if not update.effective_user or not update.effective_chat:
            return

        try:
            user = User(
                chat_id=update.effective_chat.id,
                username=update.effective_user.username,
                first_name=update.effective_user.first_name,
                last_name=update.effective_user.last_name,
                subscribed=True,
            )

            await self._save_user(user)

            message = (
                "✅ <b>Subscribed!</b>\n\n"
                "You'll now receive notifications 5 minutes before each Mariners game starts. "
                "I'll send you the game details and a direct link to MLB Gameday.\n\n"
                "Use /unsubscribe if you want to stop receiving notifications."
            )

            if update.message:
                await update.message.reply_text(message, parse_mode=ParseMode.HTML)

            logger.info("User subscribed", chat_id=update.effective_chat.id)

        except Exception as e:
            logger.error("Error subscribing user", error=str(e))
            if update.message:
                await update.message.reply_text("Sorry, there was an error with your subscription.")

    async def _handle_unsubscribe(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unsubscribe command."""
        if not update.effective_user or not update.effective_chat:
            return

        try:
            user = User(
                chat_id=update.effective_chat.id,
                username=update.effective_user.username,
                first_name=update.effective_user.first_name,
                last_name=update.effective_user.last_name,
                subscribed=False,
            )

            await self._save_user(user)

            message = (
                "❌ <b>Unsubscribed</b>\n\n"
                "You won't receive game notifications anymore. "
                "Use /subscribe if you want to re-enable them.\n\n"
                "Thanks for using the Mariners bot! 🌊"
            )

            if update.message:
                await update.message.reply_text(message, parse_mode=ParseMode.HTML)

            logger.info("User unsubscribed", chat_id=update.effective_chat.id)

        except Exception as e:
            logger.error("Error unsubscribing user", error=str(e))
            if update.message:
                await update.message.reply_text("Sorry, there was an error with your request.")

    async def _handle_next_game(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /next_game command."""
        from datetime import datetime, timedelta

        from ..observability import get_tracer
        tracer = get_tracer("mariners-bot.telegram")

        with tracer.start_as_current_span("handle_next_game_command") as span:
            span.set_attribute("command", "nextgame")
            span.set_attribute("user.chat_id", str(update.effective_chat.id) if update.effective_chat else "unknown")

            try:
                async with self.db_session.get_session() as session:
                    repository = Repository(session)
                    # Check for current games first (within 2 hours of start)
                    current_games = await repository.get_current_games(within_hours=2)
                    upcoming_games = await repository.get_upcoming_games(limit=1)

                span.set_attribute("current_games_found", len(current_games))
                span.set_attribute("upcoming_games_found", len(upcoming_games))

                if current_games:
                    # Show current game in progress
                    game = current_games[0]
                    span.set_attribute("current_game.id", game.game_id)
                    span.set_attribute("current_game.opponent", game.opponent)
                    span.set_attribute("current_game.is_home", game.is_mariners_home)

                    # Convert to Pacific time for display
                    import pytz
                    pt_timezone = pytz.timezone("America/Los_Angeles")
                    game_time_pt = game.date.astimezone(pt_timezone)

                    # Calculate how long ago the game started
                    now_pt = datetime.now(pt_timezone)
                    time_since_start = now_pt - game_time_pt

                    # Determine if Mariners are home or away
                    if game.is_mariners_home:
                        matchup = f"<b>{game.opponent} @ Seattle Mariners</b>"
                        location_emoji = "🏠"
                        location_note = "Home Game"
                    else:
                        matchup = f"<b>Seattle Mariners @ {game.opponent}</b>"
                        location_emoji = "✈️"
                        location_note = "Away Game"

                    # Format time since start
                    if time_since_start < timedelta(minutes=1):
                        time_status = "🚨 <b>STARTING NOW!</b>"
                    elif time_since_start < timedelta(hours=1):
                        minutes = int(time_since_start.total_seconds() / 60)
                        time_status = f"🔴 <b>LIVE</b> - Started {minutes} min ago"
                    else:
                        hours = int(time_since_start.total_seconds() / 3600)
                        minutes = int((time_since_start.total_seconds() % 3600) / 60)
                        if minutes > 0:
                            time_status = f"🔴 <b>LIVE</b> - Started {hours}h {minutes}m ago"
                        else:
                            time_status = f"🔴 <b>LIVE</b> - Started {hours}h ago"

                    # Try to get pitching matchup
                    pitcher_info = ""
                    try:
                        from ..clients import MLBClient
                        async with MLBClient(self.settings) as mlb_client:
                            pitchers = await mlb_client.get_probable_pitchers(game.game_id)
                            if pitchers:
                                if game.is_mariners_home:
                                    mariners_pitcher = pitchers.get("home")
                                    opponent_pitcher = pitchers.get("away")
                                else:
                                    mariners_pitcher = pitchers.get("away")
                                    opponent_pitcher = pitchers.get("home")

                                if mariners_pitcher and opponent_pitcher:
                                    pitcher_info = f"🥎 <b>Pitching:</b> {mariners_pitcher} vs {opponent_pitcher}\n"
                                elif mariners_pitcher:
                                    pitcher_info = f"🥎 <b>Mariners Pitcher:</b> {mariners_pitcher}\n"
                    except Exception as e:
                        logger.warning("Failed to get pitcher information for current game", game_id=game.game_id, error=str(e))

                    message = (
                        f"⚾ <b>Current Mariners Game</b>\n\n"
                        f"🏟️ {matchup}\n"
                        f"{location_emoji} {location_note}\n"
                        f"{time_status}\n"
                        f"{pitcher_info}\n"
                        f"📅 <b>Started:</b> {game_time_pt.strftime('%A, %B %d, %Y')}\n"
                        f"🕐 <b>First Pitch:</b> {game_time_pt.strftime('%I:%M %p %Z')}\n"
                        f"📍 <b>Venue:</b> {game.venue}\n\n"
                        f"<a href=\"{game.gameday_url}\">🔗 Watch LIVE on MLB Gameday</a>\n"
                        f"<a href=\"{game.baseball_savant_url}\">📊 Advanced Analytics on Baseball Savant</a>\n\n"
                        f"Go Mariners! 🌊⚾"
                    )

                elif not upcoming_games:
                    message = (
                        "🤔 <b>No upcoming games found</b>\n\n"
                        "There are no scheduled Mariners games in the near future. "
                        "This could mean we're in the off-season or between series.\n\n"
                        "Check back later for the next game! 🌊"
                    )
                else:
                    game = upcoming_games[0]
                    span.set_attribute("game.id", game.game_id)
                    span.set_attribute("game.opponent", game.opponent)
                    span.set_attribute("game.is_home", game.is_mariners_home)

                    # Convert to Pacific time for display
                    import pytz
                    pt_timezone = pytz.timezone("America/Los_Angeles")
                    game_time_pt = game.date.astimezone(pt_timezone)

                    # Determine if Mariners are home or away
                    if game.is_mariners_home:
                        matchup = f"<b>{game.opponent} @ Seattle Mariners</b>"
                        location_emoji = "🏠"
                        location_note = "Home Game"
                    else:
                        matchup = f"<b>Seattle Mariners @ {game.opponent}</b>"
                        location_emoji = "✈️"
                        location_note = "Away Game"

                    # Calculate days until game (using Pacific Time for local context)
                    now_pt_date = datetime.now(pt_timezone).date()
                    game_date_pt = game_time_pt.date()
                    days_until = (game_date_pt - now_pt_date).days
                    span.set_attribute("game.days_until", days_until)

                    if days_until == 0:
                        time_note = "🔥 <b>TODAY!</b>"
                    elif days_until == 1:
                        time_note = "📅 <b>Tomorrow</b>"
                    elif days_until <= 7:
                        time_note = f"📅 In {days_until} days"
                    else:
                        time_note = f"📅 In {days_until} days"

                    # Try to get pitching matchup
                    pitcher_info = ""
                    try:
                        from ..clients import MLBClient
                        async with MLBClient(self.settings) as mlb_client:
                            pitchers = await mlb_client.get_probable_pitchers(game.game_id)
                            if pitchers:
                                if game.is_mariners_home:
                                    mariners_pitcher = pitchers.get("home")
                                    opponent_pitcher = pitchers.get("away")
                                else:
                                    mariners_pitcher = pitchers.get("away")
                                    opponent_pitcher = pitchers.get("home")

                                if mariners_pitcher and opponent_pitcher:
                                    pitcher_info = f"🥎 <b>Pitching:</b> {mariners_pitcher} vs {opponent_pitcher}\n"
                                elif mariners_pitcher:
                                    pitcher_info = f"🥎 <b>Mariners Pitcher:</b> {mariners_pitcher}\n"
                    except Exception as e:
                        logger.warning("Failed to get pitcher information for upcoming game", game_id=game.game_id, error=str(e))

                    message = (
                        f"⚾ <b>Next Mariners Game</b>\n\n"
                        f"🏟️ {matchup}\n"
                        f"{location_emoji} {location_note}\n"
                        f"{time_note}\n"
                        f"{pitcher_info}\n"
                        f"📅 <b>Date:</b> {game_time_pt.strftime('%A, %B %d, %Y')}\n"
                        f"🕐 <b>Time:</b> {game_time_pt.strftime('%I:%M %p %Z')}\n"
                        f"📍 <b>Venue:</b> {game.venue}\n\n"
                        f"<a href=\"{game.gameday_url}\">🔗 Watch on MLB Gameday</a>\n"
                        f"<a href=\"{game.baseball_savant_url}\">📊 Advanced Analytics on Baseball Savant</a>\n\n"
                        f"I'll send a notification 5 minutes before first pitch! 🚨"
                    )

                if update.message:
                    await update.message.reply_text(message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                    span.set_attribute("response.sent", True)

            except Exception as e:
                span.set_attribute("error", str(e))
                span.set_attribute("response.sent", False)
                logger.error("Error getting next game", error=str(e))
                if update.message:
                    await update.message.reply_text("Sorry, I couldn't get the next game info right now.")

    async def _handle_message(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle regular messages."""
        if not update.effective_user:
            return

        # Update user's last seen time
        try:
            if update.effective_user and update.effective_chat:
                user = User(
                    chat_id=update.effective_chat.id,
                    username=update.effective_user.username,
                    first_name=update.effective_user.first_name,
                    last_name=update.effective_user.last_name,
                )
                user.update_last_seen()
                await self._save_user(user)
        except Exception as e:
            logger.warning("Failed to update user last seen", error=str(e))

        # Send helpful response
        response = (
            "⚾ Thanks for your message! I'm here to notify you about Mariners games.\n\n"
            "Use /help to see what I can do, or /next_game to check the upcoming schedule!"
        )

        if update.message:
            await update.message.reply_text(response)

    async def _send_message_with_retry(
        self,
        chat_id: str,
        message: str,
        max_retries: int = 3
    ) -> bool:
        """Send a message with retry logic for rate limiting."""
        for attempt in range(max_retries):
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                return True

            except RetryAfter as e:
                # Telegram rate limiting
                # Handle both int and timedelta types for retry_after
                if isinstance(e.retry_after, int):
                    wait_time = e.retry_after + 1
                else:
                    wait_time = int(e.retry_after.total_seconds()) + 1  # Add 1 second buffer
                logger.warning(
                    "Rate limited by Telegram",
                    chat_id=chat_id,
                    wait_time=wait_time,
                    attempt=attempt + 1
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error("Max retries exceeded for rate limit", chat_id=chat_id)
                    return False

            except TelegramError as e:
                logger.error(
                    "Telegram error sending message",
                    chat_id=chat_id,
                    error=str(e),
                    attempt=attempt + 1
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    return False

            except Exception as e:
                logger.error(
                    "Unexpected error sending message",
                    chat_id=chat_id,
                    error=str(e),
                    attempt=attempt + 1
                )
                return False

        return False

    async def _save_user(self, user: User) -> None:
        """Save user to database."""
        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)
                await repository.save_user(user)

        except Exception as e:
            logger.error("Failed to save user", chat_id=user.chat_id, error=str(e))
            raise

    async def _save_notification_job(self, job: NotificationJob) -> None:
        """Save notification job to database."""
        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)
                await repository.save_notification_job(job)

        except Exception as e:
            logger.error("Failed to save notification job", job_id=job.job_id, error=str(e))

    async def _handle_transactions(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /transactions command."""
        if not update.effective_chat:
            return

        try:
            from datetime import date, timedelta

            from ..clients import MLBClient

            # Get recent transactions (last 14 days)
            start_date = date.today() - timedelta(days=14)
            end_date = date.today()

            async with MLBClient(self.settings) as mlb_client:
                transactions = await mlb_client.get_mariners_transactions(
                    start_date=start_date,
                    end_date=end_date
                )

            if not transactions:
                message = (
                    "📰 <b>Recent Mariners Transactions</b>\n\n"
                    "No transactions found in the last 14 days.\n\n"
                    "🌊 Go Mariners!"
                )
            else:
                # Sort by date (newest first) and limit to 10
                transactions.sort(key=lambda t: t.transaction_date, reverse=True)
                recent_transactions = transactions[:10]

                from ..models import Transaction
                message = Transaction.format_batch_notification_message(recent_transactions)

            if update.message:
                await update.message.reply_text(message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        except Exception as e:
            logger.error("Error getting recent transactions", error=str(e))
            if update.message:
                await update.message.reply_text("Sorry, I couldn't get the recent transactions right now.")

    async def _handle_transaction_settings(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /transaction_settings command."""
        if not update.effective_chat:
            return

        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)
                preferences = await repository.get_user_transaction_preferences(update.effective_chat.id)

            enabled_emoji = "✅"
            disabled_emoji = "❌"

            message = (
                f"⚙️ <b>Transaction Notification Settings</b>\n\n"
                f"<b>Current Preferences:</b>\n"
                f"{enabled_emoji if preferences.trades else disabled_emoji} Trades\n"
                f"{enabled_emoji if preferences.signings else disabled_emoji} Free Agent Signings\n"
                f"{enabled_emoji if preferences.injuries else disabled_emoji} Injury List Moves\n"
                f"{enabled_emoji if preferences.activations else disabled_emoji} Player Activations\n"
                f"{enabled_emoji if preferences.recalls else disabled_emoji} Recalls & Options\n"
                f"{enabled_emoji if preferences.releases else disabled_emoji} Player Releases\n"
                f"{enabled_emoji if preferences.status_changes else disabled_emoji} Status Changes\n"
                f"{enabled_emoji if preferences.other else disabled_emoji} Other Transactions\n\n"
                f"<b>Filters:</b>\n"
                f"{enabled_emoji if preferences.major_league_only else disabled_emoji} Major League Only\n\n"
                f"<b>Quick Toggle Commands:</b>\n"
                f"• /toggle_trades - Toggle trade notifications\n"
                f"• /toggle_signings - Toggle signing notifications\n"
                f"• /toggle_injuries - Toggle injury notifications\n"
                f"• /toggle_recalls - Toggle recall/option notifications\n"
                f"• /toggle_major_only - Toggle major league filter\n\n"
                f"🌊 Go Mariners!"
            )

            if update.message:
                await update.message.reply_text(message, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error("Error getting transaction settings", error=str(e))
            if update.message:
                await update.message.reply_text("Sorry, I couldn't get your transaction settings right now.")

    async def _handle_toggle_trades(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /toggle_trades command."""
        await self._toggle_preference(update, "trades", "Trade")

    async def _handle_toggle_signings(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /toggle_signings command."""
        await self._toggle_preference(update, "signings", "Free Agent Signing")

    async def _handle_toggle_injuries(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /toggle_injuries command."""
        await self._toggle_preference(update, "injuries", "Injury List")

    async def _handle_toggle_recalls(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /toggle_recalls command."""
        await self._toggle_preference(update, "recalls", "Recall/Option")

    async def _handle_toggle_major_only(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /toggle_major_only command."""
        await self._toggle_preference(update, "major_league_only", "Major League Only")

    async def _toggle_preference(self, update: Update, preference_name: str, display_name: str) -> None:
        """Toggle a specific transaction preference."""
        if not update.effective_chat:
            return

        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)

                # Get current preferences
                preferences = await repository.get_user_transaction_preferences(update.effective_chat.id)

                # Toggle the preference
                current_value = getattr(preferences, preference_name)
                setattr(preferences, preference_name, not current_value)

                # Save updated preferences
                await repository.save_user_transaction_preferences(preferences)

                # Send confirmation
                new_value = not current_value
                status = "enabled" if new_value else "disabled"
                emoji = "✅" if new_value else "❌"

                message = (
                    f"⚙️ <b>Settings Updated</b>\n\n"
                    f"{emoji} <b>{display_name}</b> notifications are now <b>{status}</b>.\n\n"
                    f"Use /transaction_settings to see all your preferences.\n\n"
                    f"🌊 Go Mariners!"
                )

                if update.message:
                    await update.message.reply_text(message, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error("Error toggling preference", preference=preference_name, error=str(e))
            if update.message:
                await update.message.reply_text(f"Sorry, I couldn't update your {display_name} preference right now.")
