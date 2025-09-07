"""User data model."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class User(BaseModel):
    """Represents a Telegram bot user."""
    
    chat_id: int = Field(..., description="Telegram chat ID")
    username: Optional[str] = Field(default=None, description="Telegram username")
    first_name: Optional[str] = Field(default=None, description="User's first name")
    last_name: Optional[str] = Field(default=None, description="User's last name")
    subscribed: bool = Field(default=True, description="Whether user is subscribed")
    timezone: str = Field(default="America/Los_Angeles", description="User's timezone")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When user joined")
    last_seen: Optional[datetime] = Field(default=None, description="Last interaction time")
    
    @property
    def display_name(self) -> str:
        """Get a display name for the user."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.first_name:
            return self.first_name
        elif self.username:
            return f"@{self.username}"
        else:
            return f"User {self.chat_id}"
    
    def update_last_seen(self) -> None:
        """Update the last seen timestamp."""
        self.last_seen = datetime.utcnow()
    
    def __str__(self) -> str:
        """String representation of the user."""
        return f"User({self.display_name}, chat_id={self.chat_id}, subscribed={self.subscribed})"
    
    class Config:
        """Pydantic configuration."""
        
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
