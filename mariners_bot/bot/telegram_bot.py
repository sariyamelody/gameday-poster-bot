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

        # Message handler for regular text
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        logger.debug("Bot handlers configured")

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                f"• /next_game - Get info about the next game\n\n"
                f"Go Mariners! 🌊"
            )

            await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)

            logger.info("User started bot", chat_id=update.effective_chat.id, username=user.username)

        except Exception as e:
            logger.error("Error handling start command", error=str(e))
            await update.message.reply_text("Sorry, there was an error processing your request.")

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        help_message = (
            "⚾ <b>Seattle Mariners Gameday Bot</b>\n\n"
            "I automatically notify you 5 minutes before each Mariners game starts!\n\n"
            "<b>Commands:</b>\n"
            "• /start - Start using the bot\n"
            "• /help - Show this help message\n"
            "• /status - Check your subscription status\n"
            "• /subscribe - Subscribe to game notifications\n"
            "• /unsubscribe - Unsubscribe from notifications\n"
            "• /next_game - Get info about the next upcoming game\n\n"
            "<b>Features:</b>\n"
            "• 🔔 Automatic notifications 5 minutes before games\n"
            "• 🔗 Direct links to MLB Gameday\n"
            "• 🏟️ Game details (opponent, venue, time)\n"
            "• 🌍 Timezone-aware (Pacific Time)\n\n"
            "Go Mariners! 🌊"
        )

        await update.message.reply_text(help_message, parse_mode=ParseMode.HTML)

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

            await update.message.reply_text(status_message, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error("Error checking user status", error=str(e))
            await update.message.reply_text("Sorry, I couldn't check your status right now.")

    async def _handle_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

            await update.message.reply_text(message, parse_mode=ParseMode.HTML)

            logger.info("User subscribed", chat_id=update.effective_chat.id)

        except Exception as e:
            logger.error("Error subscribing user", error=str(e))
            await update.message.reply_text("Sorry, there was an error with your subscription.")

    async def _handle_unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

            await update.message.reply_text(message, parse_mode=ParseMode.HTML)

            logger.info("User unsubscribed", chat_id=update.effective_chat.id)

        except Exception as e:
            logger.error("Error unsubscribing user", error=str(e))
            await update.message.reply_text("Sorry, there was an error with your request.")

    async def _handle_next_game(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /next_game command."""
        try:
            async with self.db_session.get_session() as session:
                repository = Repository(session)
                upcoming_games = await repository.get_upcoming_games(limit=1)

            if not upcoming_games:
                message = "🤔 No upcoming Mariners games found. Check back later!"
            else:
                game = upcoming_games[0]

                # Convert to Pacific time for display
                import pytz
                pt_timezone = pytz.timezone("America/Los_Angeles")
                game_time_pt = game.date.astimezone(pt_timezone)

                message = (
                    f"⚾ <b>Next Mariners Game</b>\n\n"
                    f"🏟️ <b>{game.away_team} @ {game.home_team}</b>\n"
                    f"📅 {game_time_pt.strftime('%A, %B %d, %Y')}\n"
                    f"🕐 {game_time_pt.strftime('%I:%M %p %Z')}\n"
                    f"📍 {game.venue}\n\n"
                    f"<a href=\"{game.gameday_url}\">🔗 MLB Gameday</a>"
                )

            await update.message.reply_text(message, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error("Error getting next game", error=str(e))
            await update.message.reply_text("Sorry, I couldn't get the next game info right now.")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                    disable_web_page_preview=False
                )
                return True

            except RetryAfter as e:
                # Telegram rate limiting
                wait_time = e.retry_after + 1  # Add 1 second buffer
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
