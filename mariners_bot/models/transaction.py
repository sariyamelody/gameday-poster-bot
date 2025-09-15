"""MLB transaction data model."""

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class TransactionType(Enum):
    """MLB transaction types."""
    
    TRADE = "TR"                    # Trade
    SIGNED_FREE_AGENT = "SFA"      # Signed as Free Agent
    STATUS_CHANGE = "SC"           # Status Change
    SELECTED = "SEL"               # Selected
    RECALLED = "REC"               # Recalled
    OPTIONED = "OPT"               # Optioned
    DESIGNATED = "DES"             # Designated for Assignment
    RELEASED = "REL"               # Released
    SUSPENDED = "SUS"              # Suspended
    PURCHASED = "PUR"              # Purchased
    CLAIMED = "CLA"                # Claimed
    REINSTATED = "REI"             # Reinstated
    INJURED_LIST = "IL"            # Injured List
    ACTIVATED = "ACT"              # Activated
    OTHER = "OTH"                  # Other


class Transaction(BaseModel):
    """Represents an MLB transaction."""
    
    transaction_id: int = Field(..., description="Unique transaction ID from MLB API")
    person_id: int = Field(..., description="Player's MLB ID")
    person_name: str = Field(..., description="Player's full name")
    
    from_team_id: int | None = Field(default=None, description="Team trading away player")
    from_team_name: str | None = Field(default=None, description="Team name trading away player")
    
    to_team_id: int | None = Field(default=None, description="Team acquiring player")
    to_team_name: str | None = Field(default=None, description="Team name acquiring player")
    
    transaction_date: date = Field(..., description="Date of transaction")
    effective_date: date | None = Field(default=None, description="Effective date of transaction")
    resolution_date: date | None = Field(default=None, description="Resolution date of transaction")
    
    type_code: str = Field(..., description="Transaction type code")
    type_description: str = Field(..., description="Human readable transaction type")
    description: str = Field(..., description="Full transaction description")
    
    @property
    def transaction_type(self) -> TransactionType:
        """Get the transaction type enum."""
        try:
            return TransactionType(self.type_code)
        except ValueError:
            return TransactionType.OTHER
    
    @property
    def is_mariners_transaction(self) -> bool:
        """Check if this transaction involves the Mariners."""
        mariners_id = 136
        return (
            self.from_team_id == mariners_id or 
            self.to_team_id == mariners_id
        )
    
    @property
    def is_mariners_acquisition(self) -> bool:
        """Check if this is the Mariners acquiring a player."""
        return self.to_team_id == 136
    
    @property
    def is_mariners_departure(self) -> bool:
        """Check if this is a player leaving the Mariners."""
        return self.from_team_id == 136
    
    @property
    def emoji(self) -> str:
        """Get an appropriate emoji for the transaction type."""
        emoji_map = {
            TransactionType.TRADE: "🔄",
            TransactionType.SIGNED_FREE_AGENT: "✍️",
            TransactionType.STATUS_CHANGE: "📋",
            TransactionType.SELECTED: "⬆️",
            TransactionType.RECALLED: "📞",
            TransactionType.OPTIONED: "⬇️",
            TransactionType.DESIGNATED: "🏷️",
            TransactionType.RELEASED: "🚪",
            TransactionType.SUSPENDED: "⏸️",
            TransactionType.PURCHASED: "💰",
            TransactionType.CLAIMED: "🎯",
            TransactionType.REINSTATED: "🔄",
            TransactionType.INJURED_LIST: "🏥",
            TransactionType.ACTIVATED: "✅",
            TransactionType.OTHER: "📝",
        }
        return emoji_map.get(self.transaction_type, "📝")
    
    def format_notification_message(self) -> str:
        """Format a notification message for this transaction."""
        emoji = self.emoji
        
        # Determine the direction emoji based on Mariners involvement
        if self.is_mariners_acquisition:
            direction_emoji = "➡️"
            team_context = "Seattle Mariners"
        elif self.is_mariners_departure:
            direction_emoji = "⬅️"
            team_context = "Seattle Mariners"
        else:
            direction_emoji = ""
            team_context = ""
        
        # Create title based on transaction type
        if self.transaction_type == TransactionType.TRADE:
            title = f"{emoji} <b>TRADE ALERT</b> {direction_emoji}"
        elif self.transaction_type == TransactionType.SIGNED_FREE_AGENT:
            title = f"{emoji} <b>FREE AGENT SIGNING</b> {direction_emoji}"
        elif self.transaction_type == TransactionType.INJURED_LIST:
            title = f"{emoji} <b>INJURY UPDATE</b>"
        elif self.transaction_type == TransactionType.ACTIVATED:
            title = f"{emoji} <b>ACTIVATION</b>"
        elif self.transaction_type == TransactionType.RECALLED:
            title = f"{emoji} <b>PLAYER RECALLED</b> {direction_emoji}"
        elif self.transaction_type == TransactionType.OPTIONED:
            title = f"{emoji} <b>PLAYER OPTIONED</b> {direction_emoji}"
        else:
            title = f"{emoji} <b>{self.type_description.upper()}</b> {direction_emoji}"
        
        # Create the message
        message = (
            f"{title}\n\n"
            f"👤 <b>Player:</b> {self.person_name}\n"
            f"📋 <b>Transaction:</b> {self.description}\n"
            f"📅 <b>Date:</b> {self.transaction_date.strftime('%B %d, %Y')}\n"
        )
        
        if self.effective_date and self.effective_date != self.transaction_date:
            message += f"⏰ <b>Effective:</b> {self.effective_date.strftime('%B %d, %Y')}\n"
        
        # Add footer
        message += "\n🌊 Go Mariners!"
        
        return message
    
    def __str__(self) -> str:
        """String representation of the transaction."""
        return f"{self.person_name} - {self.type_description} ({self.transaction_date})"

    @staticmethod
    def format_batch_notification_message(transactions: list["Transaction"]) -> str:
        """Format a batch notification message for multiple transactions."""
        if not transactions:
            return ""
        
        if len(transactions) == 1:
            return transactions[0].format_notification_message()
        
        # Count transactions by type for summary
        type_counts = {}
        for transaction in transactions:
            type_desc = transaction.type_description
            type_counts[type_desc] = type_counts.get(type_desc, 0) + 1
        
        # Create summary line
        summary_parts = []
        for type_desc, count in sorted(type_counts.items()):
            if count == 1:
                summary_parts.append(type_desc)
            else:
                summary_parts.append(f"{count} {type_desc}s")
        
        summary = " • ".join(summary_parts)
        
        # Determine date range
        dates = [t.transaction_date for t in transactions]
        min_date = min(dates)
        max_date = max(dates)
        
        if min_date == max_date:
            date_range = min_date.strftime('%B %d, %Y')
        else:
            date_range = f"{min_date.strftime('%B %d')} - {max_date.strftime('%B %d, %Y')}"
        
        # Create header
        message = (
            f"🔥 <b>MARINERS TRANSACTION UPDATE</b>\n\n"
            f"📋 <b>Summary:</b> {summary}\n"
            f"📅 <b>Date:</b> {date_range}\n\n"
            f"<b>Details:</b>\n"
        )
        
        # Add individual transaction details
        for i, transaction in enumerate(transactions, 1):
            emoji = transaction.emoji
            
            # Determine direction for Mariners
            if transaction.is_mariners_acquisition:
                direction = "➡️"
            elif transaction.is_mariners_departure:
                direction = "⬅️"
            else:
                direction = ""
            
            message += (
                f"\n{i}. {emoji} <b>{transaction.person_name}</b> {direction}\n"
                f"   {transaction.description}\n"
            )
            
            # Add effective date if different from transaction date
            if transaction.effective_date and transaction.effective_date != transaction.transaction_date:
                message += f"   <i>Effective: {transaction.effective_date.strftime('%B %d, %Y')}</i>\n"
        
        # Add footer
        message += "\n🌊 Go Mariners!"
        
        return message
