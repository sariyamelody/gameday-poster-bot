"""SQLAlchemy database models."""


from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


class GameRecord(Base):
    """SQLAlchemy model for games table."""

    __tablename__ = "games"

    game_id = Column(String, primary_key=True, index=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    venue = Column(String)
    status = Column(String, default="scheduled")
    game_type = Column(String, default="R", index=True)  # R=Regular, S=Spring, P=Postseason, W=World Series
    notification_sent = Column(Boolean, default=False, index=True)
    final_score_sent = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        """String representation of the game record."""
        return f"<GameRecord(game_id={self.game_id}, teams={self.away_team} @ {self.home_team})>"


class NotificationJobRecord(Base):
    """SQLAlchemy model for notification jobs table."""

    __tablename__ = "notification_jobs"

    id = Column(String, primary_key=True, index=True)
    game_id = Column(String, nullable=False, index=True)
    scheduled_time = Column(DateTime(timezone=True), nullable=False, index=True)
    message = Column(Text, nullable=False)
    status = Column(String, default="pending", index=True)
    chat_id = Column(String)
    attempts = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=func.now())
    sent_at = Column(DateTime(timezone=True))

    def __repr__(self) -> str:
        """String representation of the notification job record."""
        return f"<NotificationJobRecord(id={self.id}, game_id={self.game_id}, status={self.status})>"


class UserRecord(Base):
    """SQLAlchemy model for users table."""

    __tablename__ = "users"

    chat_id = Column(Integer, primary_key=True, index=True)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    subscribed = Column(Boolean, default=True, index=True)
    timezone = Column(String, default="America/Los_Angeles")
    created_at = Column(DateTime(timezone=True), default=func.now())
    last_seen = Column(DateTime(timezone=True))

    def __repr__(self) -> str:
        """String representation of the user record."""
        return f"<UserRecord(chat_id={self.chat_id}, username={self.username})>"


class TransactionRecord(Base):
    """SQLAlchemy model for MLB transactions table."""

    __tablename__ = "transactions"

    transaction_id = Column(Integer, primary_key=True, index=True)
    person_id = Column(Integer, nullable=False, index=True)
    person_name = Column(String, nullable=False)

    from_team_id = Column(Integer, index=True)
    from_team_name = Column(String)
    to_team_id = Column(Integer, index=True)
    to_team_name = Column(String)

    transaction_date = Column(DateTime(timezone=True), nullable=False, index=True)
    effective_date = Column(DateTime(timezone=True))
    resolution_date = Column(DateTime(timezone=True))

    type_code = Column(String, nullable=False, index=True)
    type_description = Column(String, nullable=False)
    description = Column(Text, nullable=False)

    notification_sent = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), default=func.now())

    def __repr__(self) -> str:
        """String representation of the transaction record."""
        return f"<TransactionRecord(id={self.transaction_id}, player={self.person_name}, type={self.type_code})>"


class UserTransactionPreference(Base):
    """SQLAlchemy model for user transaction notification preferences."""

    __tablename__ = "user_transaction_preferences"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, nullable=False, index=True)

    # Transaction type preferences
    trades = Column(Boolean, default=True)
    signings = Column(Boolean, default=True)
    recalls = Column(Boolean, default=True)
    options = Column(Boolean, default=True)
    injuries = Column(Boolean, default=True)
    activations = Column(Boolean, default=True)
    releases = Column(Boolean, default=False)
    status_changes = Column(Boolean, default=False)
    other = Column(Boolean, default=False)

    # Only major league transactions (vs minor league)
    major_league_only = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        """String representation of the user preference record."""
        return f"<UserTransactionPreference(chat_id={self.chat_id})>"


class PlayByPlaySessionRecord(Base):
    """Tracks an active play-by-play session for a live game."""

    __tablename__ = "playbyplay_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    game_pk: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_play_index: Mapped[int] = mapped_column(Integer, default=-1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<PlayByPlaySessionRecord(game_id={self.game_id}, active={self.active})>"


class InningPostRecord(Base):
    """A single inning-half post in the play-by-play channel."""

    __tablename__ = "inning_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    inning: Mapped[int] = mapped_column(Integer, nullable=False)
    half: Mapped[str] = mapped_column(String, nullable=False)           # 'top' or 'bottom'
    channel_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # message_id in the channel
    group_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)     # thread-root message_id in the group
    footer_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)    # end-of-inning summary message_id
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (UniqueConstraint("game_id", "inning", "half"),)

    def __repr__(self) -> str:
        return f"<InningPostRecord(game_id={self.game_id}, inning={self.inning}, half={self.half})>"


class PlayMessageRecord(Base):
    """A single play posted to the group discussion thread."""

    __tablename__ = "play_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    at_bat_index: Mapped[int] = mapped_column(Integer, nullable=False)  # MLB allPlays index, stable per play
    group_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_description: Mapped[str] = mapped_column(Text, nullable=False)
    last_event: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=func.now())

    __table_args__ = (UniqueConstraint("game_id", "at_bat_index"),)

    def __repr__(self) -> str:
        return f"<PlayMessageRecord(game_id={self.game_id}, at_bat_index={self.at_bat_index})>"
