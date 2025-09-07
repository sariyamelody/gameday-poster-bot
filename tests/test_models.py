"""Tests for data models."""

from datetime import UTC, datetime

from mariners_bot.models import (
    Game,
    GameStatus,
    NotificationJob,
    NotificationStatus,
    User,
)


class TestGame:
    """Test Game model."""

    def test_game_creation(self) -> None:
        """Test creating a game instance."""
        game = Game(
            game_id="12345",
            date=datetime(2025, 9, 7, 16, 5, tzinfo=UTC),
            home_team="Atlanta Braves",
            away_team="Seattle Mariners",
            venue="Truist Park",
            status=GameStatus.SCHEDULED
        )

        assert game.game_id == "12345"
        assert game.home_team == "Atlanta Braves"
        assert game.away_team == "Seattle Mariners"
        assert game.venue == "Truist Park"
        assert game.status == GameStatus.SCHEDULED
        assert not game.notification_sent

    def test_gameday_url_property(self) -> None:
        """Test gameday URL generation."""
        game = Game(
            game_id="776428",
            date=datetime.now(UTC),
            home_team="Atlanta Braves",
            away_team="Seattle Mariners",
            venue="Truist Park"
        )

        assert game.gameday_url == "https://www.mlb.com/gameday/776428"

    def test_mariners_home_detection(self) -> None:
        """Test detection of Mariners home games."""
        home_game = Game(
            game_id="12345",
            date=datetime.now(UTC),
            home_team="Seattle Mariners",
            away_team="Boston Red Sox",
            venue="T-Mobile Park"
        )

        assert home_game.is_mariners_home
        assert not home_game.is_mariners_away
        assert home_game.is_mariners_game
        assert home_game.opponent == "Boston Red Sox"

    def test_mariners_away_detection(self) -> None:
        """Test detection of Mariners away games."""
        away_game = Game(
            game_id="12345",
            date=datetime.now(UTC),
            home_team="Atlanta Braves",
            away_team="Seattle Mariners",
            venue="Truist Park"
        )

        assert not away_game.is_mariners_home
        assert away_game.is_mariners_away
        assert away_game.is_mariners_game
        assert away_game.opponent == "Atlanta Braves"

    def test_non_mariners_game(self) -> None:
        """Test detection of non-Mariners games."""
        other_game = Game(
            game_id="12345",
            date=datetime.now(UTC),
            home_team="Boston Red Sox",
            away_team="New York Yankees",
            venue="Fenway Park"
        )

        assert not other_game.is_mariners_home
        assert not other_game.is_mariners_away
        assert not other_game.is_mariners_game


class TestNotificationJob:
    """Test NotificationJob model."""

    def test_notification_job_creation(self) -> None:
        """Test creating a notification job."""
        job = NotificationJob(
            game_id="12345",
            scheduled_time=datetime(2025, 9, 7, 16, 0, tzinfo=UTC),
            message="Test notification",
            chat_id="123456789"
        )

        assert job.game_id == "12345"
        assert job.message == "Test notification"
        assert job.status == NotificationStatus.PENDING
        assert job.attempts == 0
        assert job.sent_at is None

    def test_job_id_generation(self) -> None:
        """Test job ID generation."""
        job = NotificationJob(
            game_id="12345",
            scheduled_time=datetime.now(UTC),
            message="Test"
        )

        assert job.job_id == "mariners_game_12345"

        # Test with custom ID
        job_with_id = NotificationJob(
            id="custom_id",
            game_id="12345",
            scheduled_time=datetime.now(UTC),
            message="Test"
        )

        assert job_with_id.job_id == "custom_id"

    def test_mark_sent(self) -> None:
        """Test marking notification as sent."""
        job = NotificationJob(
            game_id="12345",
            scheduled_time=datetime.now(UTC),
            message="Test"
        )

        job.mark_sent()

        assert job.status == NotificationStatus.SENT
        assert job.sent_at is not None

    def test_mark_failed(self) -> None:
        """Test marking notification as failed."""
        job = NotificationJob(
            game_id="12345",
            scheduled_time=datetime.now(UTC),
            message="Test"
        )

        job.mark_failed("Connection error")

        assert job.status == NotificationStatus.FAILED
        assert job.error_message == "Connection error"
        assert job.attempts == 1


class TestUser:
    """Test User model."""

    def test_user_creation(self) -> None:
        """Test creating a user."""
        user = User(
            chat_id=123456789,
            username="testuser",
            first_name="Test",
            last_name="User"
        )

        assert user.chat_id == 123456789
        assert user.username == "testuser"
        assert user.first_name == "Test"
        assert user.last_name == "User"
        assert user.subscribed is True  # Default
        assert user.timezone == "America/Los_Angeles"  # Default

    def test_display_name(self) -> None:
        """Test display name generation."""
        # Full name
        user1 = User(chat_id=1, first_name="John", last_name="Doe")
        assert user1.display_name == "John Doe"

        # First name only
        user2 = User(chat_id=2, first_name="Jane")
        assert user2.display_name == "Jane"

        # Username only
        user3 = User(chat_id=3, username="testuser")
        assert user3.display_name == "@testuser"

        # Chat ID fallback
        user4 = User(chat_id=4)
        assert user4.display_name == "User 4"

    def test_update_last_seen(self) -> None:
        """Test updating last seen timestamp."""
        user = User(chat_id=123)
        assert user.last_seen is None

        user.update_last_seen()
        assert user.last_seen is not None
