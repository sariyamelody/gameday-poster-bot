"""Tests for transaction client functionality."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, patch

from mariners_bot.clients.mlb_client import MLBClient
from mariners_bot.config import get_settings
from mariners_bot.models.transaction import Transaction, TransactionType


class TestMLBClientTransactions:
    """Test the MLB client transaction functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.settings = get_settings()

    @pytest.fixture
    def mock_transaction_response(self):
        """Mock response for transaction API."""
        return {
            "transactions": [
                {
                    "id": 123456,
                    "person": {
                        "id": 789,
                        "fullName": "Test Player"
                    },
                    "toTeam": {
                        "id": 136,
                        "name": "Seattle Mariners"
                    },
                    "date": "2025-01-15",
                    "effectiveDate": "2025-01-16",
                    "typeCode": "SFA",
                    "typeDesc": "Signed as Free Agent",
                    "description": "Seattle Mariners signed free agent Test Player."
                },
                {
                    "id": 123457,
                    "person": {
                        "id": 790,
                        "fullName": "Trade Player"
                    },
                    "fromTeam": {
                        "id": 136,
                        "name": "Seattle Mariners"
                    },
                    "toTeam": {
                        "id": 137,
                        "name": "San Francisco Giants"
                    },
                    "date": "2025-01-15",
                    "typeCode": "TR",
                    "typeDesc": "Trade",
                    "description": "Seattle Mariners traded Trade Player to San Francisco Giants."
                }
            ]
        }

    @pytest.mark.asyncio
    async def test_parse_transaction_data_complete(self, mock_transaction_response):
        """Test parsing complete transaction data."""
        async with MLBClient(self.settings) as client:
            transaction_data = mock_transaction_response["transactions"][0]
            transaction = client._parse_transaction_data(transaction_data)
            
            assert transaction is not None
            assert transaction.transaction_id == 123456
            assert transaction.person_id == 789
            assert transaction.person_name == "Test Player"
            assert transaction.to_team_id == 136
            assert transaction.to_team_name == "Seattle Mariners"
            assert transaction.from_team_id is None
            assert transaction.transaction_date == date(2025, 1, 15)
            assert transaction.effective_date == date(2025, 1, 16)
            assert transaction.type_code == "SFA"
            assert transaction.type_description == "Signed as Free Agent"
            assert transaction.transaction_type == TransactionType.SIGNED_FREE_AGENT

    @pytest.mark.asyncio
    async def test_parse_transaction_data_trade(self, mock_transaction_response):
        """Test parsing trade transaction data."""
        async with MLBClient(self.settings) as client:
            transaction_data = mock_transaction_response["transactions"][1]
            transaction = client._parse_transaction_data(transaction_data)
            
            assert transaction is not None
            assert transaction.transaction_id == 123457
            assert transaction.person_name == "Trade Player"
            assert transaction.from_team_id == 136
            assert transaction.from_team_name == "Seattle Mariners"
            assert transaction.to_team_id == 137
            assert transaction.to_team_name == "San Francisco Giants"
            assert transaction.transaction_type == TransactionType.TRADE

    @pytest.mark.asyncio
    async def test_parse_transaction_data_minimal(self):
        """Test parsing transaction with minimal data."""
        minimal_data = {
            "id": 999,
            "person": {
                "id": 888,
                "fullName": "Minimal Player"
            },
            "date": "2025-01-15",
            "typeCode": "SC",
            "typeDesc": "Status Change",
            "description": "Status change for Minimal Player."
        }
        
        async with MLBClient(self.settings) as client:
            transaction = client._parse_transaction_data(minimal_data)
            
            assert transaction is not None
            assert transaction.transaction_id == 999
            assert transaction.person_name == "Minimal Player"
            assert transaction.from_team_id is None
            assert transaction.to_team_id is None
            assert transaction.effective_date is None
            assert transaction.resolution_date is None

    @pytest.mark.asyncio
    async def test_parse_transaction_data_invalid(self):
        """Test parsing invalid transaction data."""
        invalid_data = {
            "id": 999,
            # Missing required "person" field
            "date": "2025-01-15",
            "typeCode": "SC",
            "typeDesc": "Status Change",
            "description": "Invalid transaction."
        }
        
        async with MLBClient(self.settings) as client:
            transaction = client._parse_transaction_data(invalid_data)
            
            assert transaction is None

    @pytest.mark.asyncio
    async def test_parse_transactions_response(self, mock_transaction_response):
        """Test parsing full transactions response."""
        async with MLBClient(self.settings) as client:
            transactions = client._parse_transactions_response(mock_transaction_response)
            
            assert len(transactions) == 2
            assert transactions[0].person_name == "Test Player"
            assert transactions[1].person_name == "Trade Player"

    @pytest.mark.asyncio
    async def test_parse_transactions_response_empty(self):
        """Test parsing empty transactions response."""
        empty_response = {"transactions": []}
        
        async with MLBClient(self.settings) as client:
            transactions = client._parse_transactions_response(empty_response)
            
            assert len(transactions) == 0

    @pytest.mark.asyncio
    async def test_parse_transactions_response_with_invalid(self, mock_transaction_response):
        """Test parsing response with some invalid transactions."""
        # Add invalid transaction to response
        mock_transaction_response["transactions"].append({
            "id": 999,
            # Missing required fields
            "date": "2025-01-15"
        })
        
        async with MLBClient(self.settings) as client:
            transactions = client._parse_transactions_response(mock_transaction_response)
            
            # Should return only valid transactions
            assert len(transactions) == 2
            assert all(t.person_name in ["Test Player", "Trade Player"] for t in transactions)

    @pytest.mark.asyncio
    @patch('mariners_bot.clients.mlb_client.MLBClient._make_request')
    async def test_get_team_transactions(self, mock_request, mock_transaction_response):
        """Test getting team transactions."""
        mock_request.return_value = mock_transaction_response
        
        async with MLBClient(self.settings) as client:
            transactions = await client.get_team_transactions(
                team_id=136,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 31)
            )
            
            assert len(transactions) == 2
            mock_request.assert_called_once_with(
                "transactions",
                params={
                    "teamId": 136,
                    "startDate": "2025-01-01",
                    "endDate": "2025-01-31"
                }
            )

    @pytest.mark.asyncio
    @patch('mariners_bot.clients.mlb_client.MLBClient._make_request')
    async def test_get_mariners_transactions(self, mock_request, mock_transaction_response):
        """Test getting Mariners transactions."""
        mock_request.return_value = mock_transaction_response
        
        async with MLBClient(self.settings) as client:
            transactions = await client.get_mariners_transactions(
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 31)
            )
            
            assert len(transactions) == 2
            # Should use Mariners team ID (136)
            mock_request.assert_called_once_with(
                "transactions",
                params={
                    "teamId": 136,
                    "startDate": "2025-01-01",
                    "endDate": "2025-01-31"
                }
            )

    @pytest.mark.asyncio
    @patch('mariners_bot.clients.mlb_client.MLBClient._make_request')
    async def test_get_transactions_no_dates(self, mock_request, mock_transaction_response):
        """Test getting transactions without date parameters."""
        mock_request.return_value = mock_transaction_response
        
        async with MLBClient(self.settings) as client:
            transactions = await client.get_team_transactions(team_id=136)
            
            assert len(transactions) == 2
            mock_request.assert_called_once_with(
                "transactions",
                params={"teamId": 136}
            )

    @pytest.mark.asyncio
    @patch('mariners_bot.clients.mlb_client.MLBClient._make_request')
    async def test_get_transactions_api_error(self, mock_request):
        """Test handling API errors."""
        mock_request.side_effect = Exception("API Error")
        
        async with MLBClient(self.settings) as client:
            with pytest.raises(Exception, match="API Error"):
                await client.get_team_transactions(team_id=136)

    def test_transaction_properties(self):
        """Test transaction property methods."""
        # Mariners acquisition
        acquisition = Transaction(
            transaction_id=1,
            person_id=1,
            person_name="New Player",
            to_team_id=136,
            to_team_name="Seattle Mariners",
            transaction_date=date.today(),
            type_code="SFA",
            type_description="Signed as Free Agent",
            description="Mariners signed player."
        )
        
        assert acquisition.is_mariners_transaction is True
        assert acquisition.is_mariners_acquisition is True
        assert acquisition.is_mariners_departure is False

        # Mariners departure
        departure = Transaction(
            transaction_id=2,
            person_id=2,
            person_name="Departing Player",
            from_team_id=136,
            from_team_name="Seattle Mariners",
            to_team_id=137,
            to_team_name="San Francisco Giants",
            transaction_date=date.today(),
            type_code="TR",
            type_description="Trade",
            description="Mariners traded player."
        )
        
        assert departure.is_mariners_transaction is True
        assert departure.is_mariners_acquisition is False
        assert departure.is_mariners_departure is True

        # Non-Mariners transaction
        other = Transaction(
            transaction_id=3,
            person_id=3,
            person_name="Other Player",
            from_team_id=137,
            from_team_name="San Francisco Giants",
            to_team_id=138,
            to_team_name="Los Angeles Dodgers",
            transaction_date=date.today(),
            type_code="TR",
            type_description="Trade",
            description="Giants traded player."
        )
        
        assert other.is_mariners_transaction is False
        assert other.is_mariners_acquisition is False
        assert other.is_mariners_departure is False
