"""Tests for transaction batching functionality."""

import pytest
from datetime import date, datetime

from mariners_bot.models.transaction import Transaction, TransactionType
from mariners_bot.scheduler.transaction_scheduler import TransactionNotificationBatcher


class TestTransactionNotificationBatcher:
    """Test the transaction notification batching system."""

    def setup_method(self):
        """Set up test fixtures."""
        self.batcher = TransactionNotificationBatcher(batch_window_minutes=10)
        self.chat_id = 12345

    def test_should_batch_first_notification(self):
        """Test that first notification is not batched."""
        transaction = self._create_test_transaction(1, "Test Player")
        
        should_batch = self.batcher.should_batch_notification(self.chat_id, transaction)
        
        assert should_batch is False

    def test_should_batch_within_window(self):
        """Test batching within the time window."""
        transaction1 = self._create_test_transaction(1, "Player One")
        transaction2 = self._create_test_transaction(2, "Player Two")
        
        # Send first notification
        self.batcher.mark_notification_sent(self.chat_id)
        self.batcher.add_transaction_to_batch(self.chat_id, transaction1)
        
        # Check if second transaction should be batched
        should_batch = self.batcher.should_batch_notification(self.chat_id, transaction2)
        
        assert should_batch is True

    def test_add_and_retrieve_batch(self):
        """Test adding transactions to batch and retrieving them."""
        transaction1 = self._create_test_transaction(1, "Player One")
        transaction2 = self._create_test_transaction(2, "Player Two")
        
        # Add transactions to batch
        self.batcher.add_transaction_to_batch(self.chat_id, transaction1)
        self.batcher.add_transaction_to_batch(self.chat_id, transaction2)
        
        # Retrieve batch
        batch = self.batcher.get_and_clear_batch(self.chat_id)
        
        assert len(batch) == 2
        assert batch[0].person_name == "Player One"
        assert batch[1].person_name == "Player Two"
        
        # Batch should be cleared after retrieval
        empty_batch = self.batcher.get_and_clear_batch(self.chat_id)
        assert len(empty_batch) == 0

    def test_mark_notification_sent(self):
        """Test marking notification as sent."""
        before_time = datetime.now()
        self.batcher.mark_notification_sent(self.chat_id)
        after_time = datetime.now()
        
        last_notification_time = self.batcher.last_notification_time[self.chat_id]
        assert before_time <= last_notification_time <= after_time

    def test_group_transactions_by_priority(self):
        """Test grouping transactions by priority."""
        transactions = [
            self._create_test_transaction(1, "Trade Player", "TR", "Trade"),
            self._create_test_transaction(2, "Free Agent", "SFA", "Signed as Free Agent"),
            self._create_test_transaction(3, "Recalled Player", "REC", "Recalled"),
            self._create_test_transaction(4, "Status Player", "SC", "Status Change"),
        ]
        
        groups = TransactionNotificationBatcher.group_transactions_by_priority(transactions)
        
        assert len(groups["high_priority"]) == 2  # Trade and Free Agent
        assert len(groups["medium_priority"]) == 1  # Recalled
        assert len(groups["low_priority"]) == 1  # Status Change
        
        # Check specific assignments
        high_priority_names = [t.person_name for t in groups["high_priority"]]
        assert "Trade Player" in high_priority_names
        assert "Free Agent" in high_priority_names

    def test_should_separate_batch_mixed_priority(self):
        """Test batch separation logic with mixed priority transactions."""
        transactions = [
            self._create_test_transaction(1, "Trade Player", "TR", "Trade"),
            self._create_test_transaction(2, "Status Player", "SC", "Status Change"),
        ]
        
        should_separate = TransactionNotificationBatcher.should_separate_batch(transactions)
        assert should_separate is True

    def test_should_not_separate_batch_same_priority(self):
        """Test batch separation logic with same priority transactions."""
        transactions = [
            self._create_test_transaction(1, "Trade Player 1", "TR", "Trade"),
            self._create_test_transaction(2, "Trade Player 2", "TR", "Trade"),
        ]
        
        should_separate = TransactionNotificationBatcher.should_separate_batch(transactions)
        assert should_separate is False

    def test_should_separate_batch_too_many(self):
        """Test batch separation with too many transactions."""
        transactions = [
            self._create_test_transaction(i, f"Player {i}", "TR", "Trade")
            for i in range(1, 7)  # 6 transactions
        ]
        
        should_separate = TransactionNotificationBatcher.should_separate_batch(transactions)
        assert should_separate is True

    def test_split_transactions_for_batching_simple(self):
        """Test splitting transactions into batches - simple case."""
        transactions = [
            self._create_test_transaction(1, "Player 1", "TR", "Trade"),
            self._create_test_transaction(2, "Player 2", "TR", "Trade"),
        ]
        
        batches = TransactionNotificationBatcher.split_transactions_for_batching(transactions)
        
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_split_transactions_for_batching_mixed_priority(self):
        """Test splitting transactions with mixed priorities."""
        transactions = [
            self._create_test_transaction(1, "Trade Player", "TR", "Trade"),
            self._create_test_transaction(2, "Status Player", "SC", "Status Change"),
            self._create_test_transaction(3, "Free Agent", "SFA", "Signed as Free Agent"),
        ]
        
        batches = TransactionNotificationBatcher.split_transactions_for_batching(transactions)
        
        # Should be split into high priority and medium/low priority batches
        assert len(batches) == 2
        
        # High priority batch should contain trade and free agent
        high_priority_batch = batches[0]
        high_priority_names = [t.person_name for t in high_priority_batch]
        assert "Trade Player" in high_priority_names
        assert "Free Agent" in high_priority_names
        
        # Low priority batch should contain status change
        low_priority_batch = batches[1]
        assert low_priority_batch[0].person_name == "Status Player"

    def test_split_transactions_large_batch(self):
        """Test splitting a large batch of transactions."""
        transactions = [
            self._create_test_transaction(i, f"Player {i}", "SC", "Status Change")
            for i in range(1, 12)  # 11 transactions
        ]
        
        batches = TransactionNotificationBatcher.split_transactions_for_batching(transactions)
        
        # Should be split into chunks of 5
        assert len(batches) == 3
        assert len(batches[0]) == 5
        assert len(batches[1]) == 5
        assert len(batches[2]) == 1

    def test_split_transactions_empty_list(self):
        """Test splitting empty transaction list."""
        batches = TransactionNotificationBatcher.split_transactions_for_batching([])
        assert batches == []

    def test_split_transactions_single_transaction(self):
        """Test splitting single transaction."""
        transactions = [self._create_test_transaction(1, "Player 1", "TR", "Trade")]
        
        batches = TransactionNotificationBatcher.split_transactions_for_batching(transactions)
        
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_get_users_with_pending_batches_empty(self):
        """Test getting users with pending batches when none exist."""
        users = self.batcher.get_users_with_pending_batches()
        assert users == []

    def test_get_users_with_pending_batches_with_data(self):
        """Test getting users with pending batches."""
        # Add some transactions to batches
        transaction = self._create_test_transaction(1, "Test Player")
        self.batcher.add_transaction_to_batch(self.chat_id, transaction)
        
        # Mark that we sent a notification a while ago (to trigger batch send)
        old_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.batcher.last_notification_time[self.chat_id] = old_time
        
        users = self.batcher.get_users_with_pending_batches()
        assert self.chat_id in users

    def _create_test_transaction(self, transaction_id: int, person_name: str, 
                                type_code: str = "SFA", type_description: str = "Signed as Free Agent") -> Transaction:
        """Helper method to create test transactions."""
        return Transaction(
            transaction_id=transaction_id,
            person_id=transaction_id + 1000,
            person_name=person_name,
            to_team_id=136,
            to_team_name="Seattle Mariners",
            transaction_date=date.today(),
            type_code=type_code,
            type_description=type_description,
            description=f"Seattle Mariners transaction for {person_name}."
        )
