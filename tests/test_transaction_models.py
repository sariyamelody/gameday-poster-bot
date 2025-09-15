"""Tests for transaction models."""

import pytest
from datetime import date

from mariners_bot.models.transaction import Transaction, TransactionType
from mariners_bot.models.user_preferences import UserTransactionPreferences


class TestTransaction:
    """Test the Transaction model."""

    def test_transaction_creation(self):
        """Test creating a transaction."""
        transaction = Transaction(
            transaction_id=123,
            person_id=456,
            person_name="Test Player",
            to_team_id=136,
            to_team_name="Seattle Mariners",
            transaction_date=date(2025, 1, 15),
            type_code="SFA",
            type_description="Signed as Free Agent",
            description="Seattle Mariners signed Test Player as a free agent."
        )
        
        assert transaction.transaction_id == 123
        assert transaction.person_name == "Test Player"
        assert transaction.transaction_type == TransactionType.SIGNED_FREE_AGENT
        assert transaction.is_mariners_transaction is True
        assert transaction.is_mariners_acquisition is True
        assert transaction.is_mariners_departure is False

    def test_mariners_departure(self):
        """Test transaction where player leaves Mariners."""
        transaction = Transaction(
            transaction_id=124,
            person_id=457,
            person_name="Departing Player",
            from_team_id=136,
            from_team_name="Seattle Mariners",
            to_team_id=137,
            to_team_name="San Francisco Giants",
            transaction_date=date(2025, 1, 15),
            type_code="TR",
            type_description="Trade",
            description="Seattle Mariners traded Departing Player to San Francisco Giants."
        )
        
        assert transaction.is_mariners_transaction is True
        assert transaction.is_mariners_acquisition is False
        assert transaction.is_mariners_departure is True

    def test_non_mariners_transaction(self):
        """Test transaction not involving Mariners."""
        transaction = Transaction(
            transaction_id=125,
            person_id=458,
            person_name="Other Player",
            from_team_id=137,
            from_team_name="San Francisco Giants",
            to_team_id=138,
            to_team_name="Los Angeles Dodgers",
            transaction_date=date(2025, 1, 15),
            type_code="TR",
            type_description="Trade",
            description="San Francisco Giants traded Other Player to Los Angeles Dodgers."
        )
        
        assert transaction.is_mariners_transaction is False
        assert transaction.is_mariners_acquisition is False
        assert transaction.is_mariners_departure is False

    def test_transaction_emojis(self):
        """Test transaction emoji assignment."""
        trade = Transaction(
            transaction_id=1, person_id=1, person_name="Player", 
            transaction_date=date.today(), type_code="TR", 
            type_description="Trade", description="Test"
        )
        assert trade.emoji == "🔄"

        signing = Transaction(
            transaction_id=2, person_id=2, person_name="Player", 
            transaction_date=date.today(), type_code="SFA", 
            type_description="Signed as Free Agent", description="Test"
        )
        assert signing.emoji == "✍️"

        injury = Transaction(
            transaction_id=3, person_id=3, person_name="Player", 
            transaction_date=date.today(), type_code="IL", 
            type_description="Injured List", description="Test"
        )
        assert injury.emoji == "🏥"

    def test_single_transaction_notification_message(self):
        """Test formatting a single transaction notification."""
        transaction = Transaction(
            transaction_id=123,
            person_id=456,
            person_name="Josh Fleming",
            to_team_id=136,
            to_team_name="Seattle Mariners",
            transaction_date=date(2025, 1, 15),
            effective_date=date(2025, 1, 16),
            type_code="SFA",
            type_description="Signed as Free Agent",
            description="Seattle Mariners signed free agent LHP Josh Fleming to a minor league contract."
        )
        
        message = transaction.format_notification_message()
        
        assert "FREE AGENT SIGNING" in message
        assert "Josh Fleming" in message
        assert "January 15, 2025" in message
        assert "⏰ <b>Effective:</b> January 16, 2025" in message
        assert "Go Mariners!" in message

    def test_batch_notification_message_single(self):
        """Test batch formatting with a single transaction."""
        transaction = Transaction(
            transaction_id=123,
            person_id=456,
            person_name="Test Player",
            to_team_id=136,
            to_team_name="Seattle Mariners",
            transaction_date=date(2025, 1, 15),
            type_code="SFA",
            type_description="Signed as Free Agent",
            description="Test signing."
        )
        
        message = Transaction.format_batch_notification_message([transaction])
        
        # Single transaction should use individual format
        assert "FREE AGENT SIGNING" in message
        assert "MARINERS TRANSACTION UPDATE" not in message

    def test_batch_notification_message_multiple(self):
        """Test batch formatting with multiple transactions."""
        transactions = [
            Transaction(
                transaction_id=1,
                person_id=1,
                person_name="Player One",
                to_team_id=136,
                to_team_name="Seattle Mariners",
                transaction_date=date(2025, 1, 15),
                type_code="SFA",
                type_description="Signed as Free Agent",
                description="Signed Player One."
            ),
            Transaction(
                transaction_id=2,
                person_id=2,
                person_name="Player Two",
                from_team_id=136,
                from_team_name="Seattle Mariners",
                to_team_id=137,
                to_team_name="San Francisco Giants",
                transaction_date=date(2025, 1, 15),
                type_code="TR",
                type_description="Trade",
                description="Traded Player Two."
            )
        ]
        
        message = Transaction.format_batch_notification_message(transactions)
        
        assert "MARINERS TRANSACTION UPDATE" in message
        assert "Signed as Free Agent • Trade" in message
        assert "January 15, 2025" in message
        assert "Player One" in message
        assert "Player Two" in message
        assert "1." in message and "2." in message

    def test_batch_notification_date_range(self):
        """Test batch formatting with transactions across multiple dates."""
        transactions = [
            Transaction(
                transaction_id=1,
                person_id=1,
                person_name="Player One",
                to_team_id=136,
                to_team_name="Seattle Mariners",
                transaction_date=date(2025, 1, 15),
                type_code="SFA",
                type_description="Signed as Free Agent",
                description="Signed Player One."
            ),
            Transaction(
                transaction_id=2,
                person_id=2,
                person_name="Player Two",
                to_team_id=136,
                to_team_name="Seattle Mariners",
                transaction_date=date(2025, 1, 17),
                type_code="SFA",
                type_description="Signed as Free Agent",
                description="Signed Player Two."
            )
        ]
        
        message = Transaction.format_batch_notification_message(transactions)
        
        assert "January 15 - January 17, 2025" in message

    def test_empty_batch_message(self):
        """Test batch formatting with empty list."""
        message = Transaction.format_batch_notification_message([])
        assert message == ""


class TestUserTransactionPreferences:
    """Test the UserTransactionPreferences model."""

    def test_default_preferences(self):
        """Test default preference values."""
        prefs = UserTransactionPreferences(chat_id=12345)
        
        assert prefs.chat_id == 12345
        assert prefs.trades is True
        assert prefs.signings is True
        assert prefs.recalls is True
        assert prefs.options is True
        assert prefs.injuries is True
        assert prefs.activations is True
        assert prefs.releases is False
        assert prefs.status_changes is False
        assert prefs.other is False
        assert prefs.major_league_only is True

    def test_should_notify_for_transaction_types(self):
        """Test notification logic for different transaction types."""
        prefs = UserTransactionPreferences(
            chat_id=12345,
            trades=True,
            signings=True,
            injuries=False,
            releases=False
        )
        
        # Should notify for enabled types
        assert prefs.should_notify_for_transaction(TransactionType.TRADE, "Major league trade") is True
        assert prefs.should_notify_for_transaction(TransactionType.SIGNED_FREE_AGENT, "Major league signing") is True
        
        # Should not notify for disabled types
        assert prefs.should_notify_for_transaction(TransactionType.INJURED_LIST, "Injured list move") is False
        assert prefs.should_notify_for_transaction(TransactionType.RELEASED, "Player released") is False

    def test_major_league_only_filter(self):
        """Test major league only filtering."""
        prefs = UserTransactionPreferences(
            chat_id=12345,
            signings=True,
            major_league_only=True
        )
        
        # Should notify for major league transactions
        assert prefs.should_notify_for_transaction(
            TransactionType.SIGNED_FREE_AGENT, 
            "Seattle Mariners signed major league contract"
        ) is True
        
        # Should not notify for minor league transactions
        assert prefs.should_notify_for_transaction(
            TransactionType.SIGNED_FREE_AGENT, 
            "Seattle Mariners signed minor league contract"
        ) is False
        
        assert prefs.should_notify_for_transaction(
            TransactionType.SIGNED_FREE_AGENT, 
            "Seattle Mariners signed to Triple-A contract"
        ) is False

    def test_major_league_only_disabled(self):
        """Test with major league filter disabled."""
        prefs = UserTransactionPreferences(
            chat_id=12345,
            signings=True,
            major_league_only=False
        )
        
        # Should notify for both major and minor league transactions
        assert prefs.should_notify_for_transaction(
            TransactionType.SIGNED_FREE_AGENT, 
            "Seattle Mariners signed minor league contract"
        ) is True

    def test_preferences_summary(self):
        """Test preferences summary generation."""
        prefs = UserTransactionPreferences(
            chat_id=12345,
            trades=True,
            signings=True,
            injuries=False,
            major_league_only=True
        )
        
        summary = prefs.summary
        assert "Trades" in summary
        assert "Signings" in summary
        assert "Major League only" in summary
        assert "Injuries" not in summary

    def test_all_disabled_summary(self):
        """Test summary when no notifications are enabled."""
        prefs = UserTransactionPreferences(
            chat_id=12345,
            trades=False,
            signings=False,
            recalls=False,
            options=False,
            injuries=False,
            activations=False,
            releases=False,
            status_changes=False,
            other=False
        )
        
        summary = prefs.summary
        assert "No transaction notifications enabled" in summary

    def test_transaction_type_mapping(self):
        """Test mapping of various transaction types to preferences."""
        prefs = UserTransactionPreferences(
            chat_id=12345,
            recalls=True,
            status_changes=False,
            other=False
        )
        
        # Selected type should map to recalls
        assert prefs.should_notify_for_transaction(TransactionType.SELECTED, "Player selected") is True
        
        # Designated should map to status_changes (disabled)
        assert prefs.should_notify_for_transaction(TransactionType.DESIGNATED, "Player designated") is False
        
        # Other should map to other (disabled)
        assert prefs.should_notify_for_transaction(TransactionType.OTHER, "Unknown transaction") is False
