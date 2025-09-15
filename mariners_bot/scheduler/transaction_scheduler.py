"""Transaction monitoring scheduler."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import Settings
from ..models import Transaction, TransactionType

logger = structlog.get_logger(__name__)

# Global callback storage for scheduler jobs
_transaction_sync_callback: Callable[[], Awaitable[None]] | None = None


async def _transaction_sync_wrapper() -> None:
    """Wrapper for transaction sync with error handling."""
    try:
        if _transaction_sync_callback:
            await _transaction_sync_callback()
        else:
            logger.error("No transaction sync callback set")

    except Exception as e:
        logger.error("Error in transaction sync callback", error=str(e))


class TransactionScheduler:
    """Scheduler for monitoring MLB transactions."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the transaction scheduler."""
        self.settings = settings

        # Configure job store (reuse the same database)
        jobstores = {
            'default': SQLAlchemyJobStore(url=settings.database_url, tablename='apscheduler_jobs')
        }

        # Configure executors
        executors = {
            'default': AsyncIOExecutor()
        }

        # Job defaults
        job_defaults = {
            'coalesce': True,  # Combine multiple pending runs into one
            'max_instances': 1,  # Only allow one instance at a time
            'misfire_grace_time': 300  # 5 minutes grace period
        }

        # Initialize scheduler
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults
        )

        logger.info("Transaction scheduler initialized")

    async def start(self) -> None:
        """Start the transaction scheduler."""
        try:
            self.scheduler.start()

            # Schedule transaction sync job to run every 5 minutes
            self._schedule_transaction_sync()

            logger.info("Transaction scheduler started")

        except Exception as e:
            logger.error("Failed to start transaction scheduler", error=str(e))
            raise

    async def shutdown(self) -> None:
        """Shutdown the scheduler gracefully."""
        logger.info("Shutting down transaction scheduler")

        try:
            self.scheduler.shutdown(wait=True)
            logger.info("Transaction scheduler shutdown complete")

        except Exception as e:
            logger.error("Error during transaction scheduler shutdown", error=str(e))
            raise

    def set_transaction_sync_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Set the callback function for syncing transactions."""
        global _transaction_sync_callback
        _transaction_sync_callback = callback
        logger.debug("Transaction sync callback set")

    def _schedule_transaction_sync(self) -> None:
        """Schedule the transaction sync job."""
        global _transaction_sync_callback
        if not _transaction_sync_callback:
            logger.warning("No transaction sync callback set, skipping transaction sync scheduling")
            return

        # Schedule every 5 minutes
        self.scheduler.add_job(
            _transaction_sync_wrapper,
            trigger=IntervalTrigger(minutes=5),
            id='transaction_sync',
            replace_existing=True,
            max_instances=1
        )

        logger.info("Scheduled transaction sync job to run every 5 minutes")


class TransactionNotificationBatcher:
    """Handles batching of transaction notifications."""

    def __init__(self, batch_window_minutes: int = 10) -> None:
        """Initialize the batcher."""
        self.batch_window_minutes = batch_window_minutes
        self.pending_transactions: dict[int, list[Transaction]] = {}  # chat_id -> transactions
        self.last_notification_time: dict[int, datetime] = {}  # chat_id -> last notification time

    def should_batch_notification(self, chat_id: int, transaction: Transaction) -> bool:
        """Check if a notification should be batched or sent immediately."""
        now = datetime.now()
        
        # If this is the first transaction for this user, don't batch
        if chat_id not in self.last_notification_time:
            return False
        
        # If the last notification was sent more than batch_window ago, don't batch
        last_notification = self.last_notification_time[chat_id]
        if (now - last_notification).total_seconds() > (self.batch_window_minutes * 60):
            return False
        
        # Check if there are pending transactions for this user
        if chat_id in self.pending_transactions and self.pending_transactions[chat_id]:
            return True
        
        return False

    def add_transaction_to_batch(self, chat_id: int, transaction: Transaction) -> None:
        """Add a transaction to the pending batch for a user."""
        if chat_id not in self.pending_transactions:
            self.pending_transactions[chat_id] = []
        
        self.pending_transactions[chat_id].append(transaction)
        logger.debug("Added transaction to batch", chat_id=chat_id, transaction_id=transaction.transaction_id)

    def get_and_clear_batch(self, chat_id: int) -> list[Transaction]:
        """Get and clear the pending batch for a user."""
        if chat_id not in self.pending_transactions:
            return []
        
        transactions = self.pending_transactions[chat_id].copy()
        self.pending_transactions[chat_id] = []
        
        logger.debug("Retrieved batch for user", chat_id=chat_id, count=len(transactions))
        return transactions

    def mark_notification_sent(self, chat_id: int) -> None:
        """Mark that a notification was sent to a user."""
        self.last_notification_time[chat_id] = datetime.now()

    def get_users_with_pending_batches(self) -> list[int]:
        """Get list of chat_ids with pending transactions that should be sent."""
        now = datetime.now()
        users_to_notify = []
        
        for chat_id, transactions in self.pending_transactions.items():
            if not transactions:
                continue
            
            # Check if the oldest transaction in the batch is older than batch window
            oldest_transaction = min(transactions, key=lambda t: t.transaction_date)
            
            # If the oldest transaction is more than batch_window old, send the batch
            transaction_age_minutes = (now.date() - oldest_transaction.transaction_date).days * 24 * 60
            
            # Also check if we haven't sent a notification recently
            if chat_id in self.last_notification_time:
                time_since_last = (now - self.last_notification_time[chat_id]).total_seconds() / 60
                if time_since_last >= self.batch_window_minutes:
                    users_to_notify.append(chat_id)
            else:
                # First notification for this user
                users_to_notify.append(chat_id)
        
        return users_to_notify

    @staticmethod
    def group_transactions_by_priority(transactions: list[Transaction]) -> dict[str, list[Transaction]]:
        """Group transactions by priority for better batching."""
        groups = {
            "high_priority": [],  # Trades, major signings
            "medium_priority": [],  # Recalls, activations, injuries
            "low_priority": []  # Status changes, minor moves
        }
        
        high_priority_types = {TransactionType.TRADE, TransactionType.SIGNED_FREE_AGENT}
        medium_priority_types = {
            TransactionType.RECALLED, TransactionType.ACTIVATED, 
            TransactionType.INJURED_LIST, TransactionType.OPTIONED
        }
        
        for transaction in transactions:
            if transaction.transaction_type in high_priority_types:
                groups["high_priority"].append(transaction)
            elif transaction.transaction_type in medium_priority_types:
                groups["medium_priority"].append(transaction)
            else:
                groups["low_priority"].append(transaction)
        
        return groups

    @staticmethod
    def should_separate_batch(transactions: list[Transaction]) -> bool:
        """Check if transactions should be separated into multiple messages."""
        # Separate if there are both high-priority and low-priority transactions
        groups = TransactionNotificationBatcher.group_transactions_by_priority(transactions)
        
        has_high = len(groups["high_priority"]) > 0
        has_low = len(groups["low_priority"]) > 0
        
        # If we have both high and low priority, or more than 5 transactions total, separate
        return (has_high and has_low) or len(transactions) > 5

    @staticmethod
    def split_transactions_for_batching(transactions: list[Transaction]) -> list[list[Transaction]]:
        """Split transactions into optimal batches for notification."""
        if len(transactions) <= 1:
            return [transactions] if transactions else []
        
        if not TransactionNotificationBatcher.should_separate_batch(transactions):
            return [transactions]
        
        groups = TransactionNotificationBatcher.group_transactions_by_priority(transactions)
        batches = []
        
        # Send high priority as separate batch
        if groups["high_priority"]:
            batches.append(groups["high_priority"])
        
        # Combine medium and low priority
        medium_low = groups["medium_priority"] + groups["low_priority"]
        if medium_low:
            # Split into chunks of 5 if too many
            while len(medium_low) > 5:
                batches.append(medium_low[:5])
                medium_low = medium_low[5:]
            if medium_low:
                batches.append(medium_low)
        
        return batches
