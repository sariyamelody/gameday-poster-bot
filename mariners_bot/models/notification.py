"""Notification job data model."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NotificationStatus(str, Enum):
    """Notification job status."""
    
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NotificationJob(BaseModel):
    """Represents a scheduled notification job."""
    
    id: Optional[str] = Field(default=None, description="Job ID")
    game_id: str = Field(..., description="Associated game ID")
    scheduled_time: datetime = Field(..., description="When to send notification (UTC)")
    message: str = Field(..., description="Notification message content")
    status: NotificationStatus = Field(default=NotificationStatus.PENDING, description="Job status")
    chat_id: Optional[str] = Field(default=None, description="Telegram chat ID")
    attempts: int = Field(default=0, description="Number of send attempts")
    error_message: Optional[str] = Field(default=None, description="Last error message")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Job creation time")
    sent_at: Optional[datetime] = Field(default=None, description="When notification was sent")
    
    @property
    def job_id(self) -> str:
        """Generate a unique job ID."""
        if self.id:
            return self.id
        return f"mariners_game_{self.game_id}"
    
    def mark_sent(self) -> None:
        """Mark the notification as successfully sent."""
        self.status = NotificationStatus.SENT
        self.sent_at = datetime.utcnow()
    
    def mark_failed(self, error: str) -> None:
        """Mark the notification as failed."""
        self.status = NotificationStatus.FAILED
        self.error_message = error
        self.attempts += 1
    
    def mark_cancelled(self) -> None:
        """Mark the notification as cancelled."""
        self.status = NotificationStatus.CANCELLED
    
    def __str__(self) -> str:
        """String representation of the notification job."""
        return (
            f"NotificationJob(game_id={self.game_id}, "
            f"scheduled={self.scheduled_time.strftime('%Y-%m-%d %H:%M UTC')}, "
            f"status={self.status.value})"
        )
    
    class Config:
        """Pydantic configuration."""
        
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
