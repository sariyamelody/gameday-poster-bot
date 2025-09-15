"""User transaction preference model."""

from pydantic import BaseModel, Field

from .transaction import TransactionType


class UserTransactionPreferences(BaseModel):
    """User preferences for transaction notifications."""

    chat_id: int = Field(..., description="User's Telegram chat ID")

    # Transaction type preferences
    trades: bool = Field(default=True, description="Notify about trades")
    signings: bool = Field(default=True, description="Notify about free agent signings")
    recalls: bool = Field(default=True, description="Notify about player recalls")
    options: bool = Field(default=True, description="Notify about player options")
    injuries: bool = Field(default=True, description="Notify about injury list moves")
    activations: bool = Field(default=True, description="Notify about player activations")
    releases: bool = Field(default=False, description="Notify about player releases")
    status_changes: bool = Field(default=False, description="Notify about general status changes")
    other: bool = Field(default=False, description="Notify about other transaction types")

    # Filtering preferences
    major_league_only: bool = Field(default=True, description="Only notify about major league transactions")

    def should_notify_for_transaction(self, transaction_type: TransactionType, description: str) -> bool:
        """Check if user should be notified for this transaction type."""
        # Check if it's a minor league transaction and user only wants major league
        if self.major_league_only:
            description_lower = description.lower()
            if any(term in description_lower for term in ["minor league", "triple-a", "double-a", "single-a", "rookie"]):
                return False

        # Map transaction types to preferences
        type_preferences = {
            TransactionType.TRADE: self.trades,
            TransactionType.SIGNED_FREE_AGENT: self.signings,
            TransactionType.RECALLED: self.recalls,
            TransactionType.OPTIONED: self.options,
            TransactionType.INJURED_LIST: self.injuries,
            TransactionType.ACTIVATED: self.activations,
            TransactionType.RELEASED: self.releases,
            TransactionType.STATUS_CHANGE: self.status_changes,
            TransactionType.SELECTED: self.recalls,  # Similar to recalls
            TransactionType.DESIGNATED: self.status_changes,  # General status change
            TransactionType.SUSPENDED: self.status_changes,
            TransactionType.PURCHASED: self.signings,  # Similar to signings
            TransactionType.CLAIMED: self.signings,  # Similar to signings
            TransactionType.REINSTATED: self.activations,  # Similar to activations
            TransactionType.OTHER: self.other,
        }

        return type_preferences.get(transaction_type, self.other)

    @property
    def summary(self) -> str:
        """Get a summary of user preferences."""
        enabled = []
        if self.trades:
            enabled.append("Trades")
        if self.signings:
            enabled.append("Signings")
        if self.recalls:
            enabled.append("Recalls")
        if self.options:
            enabled.append("Options")
        if self.injuries:
            enabled.append("Injuries")
        if self.activations:
            enabled.append("Activations")
        if self.releases:
            enabled.append("Releases")
        if self.status_changes:
            enabled.append("Status Changes")
        if self.other:
            enabled.append("Other")

        if not enabled:
            return "No transaction notifications enabled"

        summary = f"Notifications enabled for: {', '.join(enabled)}"

        if self.major_league_only:
            summary += " (Major League only)"
        else:
            summary += " (All levels)"

        return summary

    def __str__(self) -> str:
        """String representation of preferences."""
        return f"TransactionPreferences(chat_id={self.chat_id}, {self.summary})"
