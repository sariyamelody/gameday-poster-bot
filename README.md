# Seattle Mariners Gameday Telegram Bot

A Telegram bot that automatically notifies you 5 minutes before Seattle Mariners games start with a direct link to MLB Gameday, plus real-time transaction monitoring with customizable notifications.

## Features

### Game Notifications
- 🔔 **Automatic Notifications**: Get notified 5 minutes before each Mariners game
- ⚾ **Game Information**: Opponent, venue, and start time details
- 🔗 **Direct Links**: One-click access to MLB Gameday
- 🏟️ **Current Game Status**: Live game detection and status updates
- 🥎 **Pitching Matchups**: Probable pitcher information

### Transaction Monitoring
- 📰 **Real-time Transactions**: Monitor MLB transactions every 5 minutes
- 🔄 **Batch Notifications**: Multiple transactions grouped into single messages
- ⚙️ **Customizable Preferences**: Users can choose which transaction types to receive
- 🏆 **Major League Filter**: Option to only receive major league transactions
- 📱 **Channel & Personal**: All transactions go to channel, users customize personal notifications

### Technical Features
- 🌍 **Timezone Aware**: Handles Pacific Time and daylight saving transitions
- 📊 **Observability**: OpenTelemetry monitoring and structured logging
- 🐳 **Docker Ready**: Containerized deployment with Docker Compose

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))

### Installation

1. Clone the repository:
```bash
git clone https://github.com/your-username/gameday-poster-bot.git
cd gameday-poster-bot
```

2. Install dependencies:
```bash
uv sync
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your Telegram bot token
```

4. Run the bot:
```bash
uv run mariners-bot
```

### Docker Deployment

```bash
docker-compose up -d
```

### Production Deployment with Honeycomb

For production deployment with Honeycomb observability:

```bash
# Copy the Honeycomb environment template
cp env.honeycomb.template .env

# Edit .env with your actual values:
# - TELEGRAM_BOT_TOKEN (from @BotFather)  
# - TELEGRAM_CHAT_ID (use mariners-bot get-chat-id)
# - OTEL_EXPORTER_OTLP_HEADERS (your Honeycomb API key)

# Run with production observability
docker-compose up -d

# Monitor traces and metrics in Honeycomb UI
```

## Bot Commands

### Game Commands
- `/start` - Start using the bot and get an introduction
- `/help` - Show comprehensive help with all commands
- `/status` - Check your current subscription status
- `/subscribe` - Subscribe to game notifications
- `/unsubscribe` - Unsubscribe from all notifications
- `/nextgame` or `/next_game` - Get info about the next upcoming game

### Transaction Commands
- `/transactions` - View recent Mariners transactions (last 14 days)
- `/transaction_settings` - View and manage transaction notification preferences
- `/toggle_trades` - Toggle trade notifications on/off
- `/toggle_signings` - Toggle free agent signing notifications on/off
- `/toggle_injuries` - Toggle injury list notifications on/off
- `/toggle_recalls` - Toggle player recall/option notifications on/off
- `/toggle_major_only` - Toggle major league only filter on/off

## Configuration

Set these environment variables:

- `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
- `TELEGRAM_CHAT_ID`: Default chat ID for notifications (optional)
- `DATABASE_URL`: Database connection string (default: SQLite)
- `LOG_LEVEL`: Logging level (default: INFO)
- `OTEL_EXPORTER_ENDPOINT`: OpenTelemetry collector endpoint (optional)

## Development

### Running Tests

```bash
uv run pytest
```

### Code Quality

```bash
uv run ruff check .
uv run mypy .
```

### Pre-commit Hooks

```bash
uv run pre-commit install
```

## Architecture

Built with modern Python practices:

- **FastAPI**: Async web framework for health checks
- **SQLAlchemy**: Database ORM with async support
- **APScheduler**: Reliable job scheduling
- **OpenTelemetry**: Observability and monitoring
- **Pydantic**: Data validation and settings
- **Structlog**: Structured logging

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## Support

- 📖 [Documentation](./PLAN.md)
- 🐛 [Issues](https://github.com/your-username/gameday-poster-bot/issues)
- 💬 [Discussions](https://github.com/your-username/gameday-poster-bot/discussions)
