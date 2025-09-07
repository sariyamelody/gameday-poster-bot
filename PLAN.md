# Seattle Mariners Gameday Telegram Bot - Technical Plan

A Telegram bot that finds the Seattle Mariners game schedule and posts a link to the MLB Gameday 5 minutes before each game starts.

## Architecture Overview

The bot follows an **event-driven architecture** with scheduled tasks:

```
[MLB API] → [Schedule Fetcher] → [Game Database] → [Scheduler] → [Telegram Bot] → [Users]
     ↑              ↓                    ↓              ↓              ↓
[Daily Sync]   [Parse Games]      [Store Games]   [Queue Jobs]   [Send Messages]
```

## Core Components

### 1. Data Models & Storage

**Game Data Structure:**
```python
@dataclass
class Game:
    game_id: str          # MLB gamePk identifier
    date: datetime        # Game date/time in UTC
    home_team: str        # Home team name
    away_team: str        # Away team name
    venue: str           # Stadium name
    gameday_url: str     # MLB Gameday URL
    notification_sent: bool = False
    
@dataclass  
class NotificationJob:
    game_id: str
    scheduled_time: datetime  # 5 minutes before game start
    message: str
    status: str  # pending, sent, failed
```

### 2. MLB Data Integration

**Primary API: MLB Stats API**
- Endpoint: `https://statsapi.mlb.com/api/v1/schedule`
- Seattle Mariners team ID: `136`
- Fetch full season schedule on startup
- Daily incremental updates for schedule changes

**Schedule Fetching Strategy:**
- Initial load: Full season schedule
- Daily sync: Check for schedule changes/additions
- Handles postponements, time changes, and makeup games

### 3. Telegram Bot Implementation

**Core Features:**
- **python-telegram-bot** library (v20+) for robust async handling
- Commands: `/start`, `/status`, `/next_game`, `/subscribe`, `/unsubscribe`
- Channel/group support with proper permissions
- Rich message formatting with game details and clickable links

**Message Format:**
```
🔥 Mariners Game Starting Soon! 
⚾ Seattle Mariners vs Boston Red Sox
🏟️ T-Mobile Park
🕐 Starts in 5 minutes (7:10 PM PT)
📺 Watch Live: [MLB Gameday](https://www.mlb.com/gameday/12345)
```

### 4. Scheduling System

**APScheduler-based approach:**
- **BackgroundScheduler** for non-blocking operation
- **SQLite JobStore** for persistence across restarts
- **ProcessPoolExecutor** for scalability
- Jobs scheduled exactly 5 minutes before game time
- Automatic timezone handling (PT/PDT for Seattle)

**Job Management:**
```python
def schedule_game_notification(game: Game):
    notification_time = game.date - timedelta(minutes=5)
    scheduler.add_job(
        send_game_notification,
        'date',
        run_date=notification_time,
        args=[game.game_id],
        id=f"game_{game.game_id}",
        replace_existing=True
    )
```

**Advanced Scheduling Features:**
- **Timezone-aware scheduling**: All times stored in UTC, converted for display
- **DST transition handling**: Automatic adjustment for spring/fall time changes  
- **Game postponement detection**: Reschedule notifications when games are delayed
- **Duplicate prevention**: Idempotent job scheduling with unique constraints
- **Graceful restart**: Jobs persist across application restarts

**Scheduling Flow:**
```python
# Daily at 6 AM PT - fetch updated schedule
scheduler.add_job(sync_schedule, 'cron', hour=6, timezone='America/Los_Angeles')

# Process each game and schedule notification
def process_games(games):
    for game in games:
        if game.is_mariners_game() and not game.notification_sent:
            schedule_notification_job(game)
            
# Handle edge cases
def schedule_notification_job(game):
    notification_time = game.start_time - timedelta(minutes=5)
    
    # Skip if game already started
    if notification_time <= datetime.now(timezone.utc):
        return
        
    # Schedule with collision detection
    job_id = f"mariners_game_{game.game_id}"
    scheduler.add_job(
        send_notification,
        'date', 
        run_date=notification_time,
        args=[game],
        id=job_id,
        replace_existing=True,
        max_instances=1
    )
```

### 5. Error Handling & Resilience

**Failure Domains & Mitigation:**

**API Reliability:**
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def fetch_schedule():
    try:
        response = await mlb_client.get_schedule(team_id=136)
        return response
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"MLB API error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching schedule: {e}")
        # Fallback to cached schedule if available
        return get_cached_schedule()
```

**Telegram Bot Resilience:**
```python
# Rate limiting with exponential backoff
async def send_notification_with_retry(chat_id, message):
    for attempt in range(3):
        try:
            await bot.send_message(chat_id, message, parse_mode='HTML')
            return True
        except telegram.error.RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except telegram.error.TelegramError as e:
            logger.error(f"Telegram error (attempt {attempt + 1}): {e}")
            if attempt == 2:  # Last attempt
                # Store in dead letter queue for manual review
                store_failed_notification(chat_id, message, str(e))
                return False
            await asyncio.sleep(2 ** attempt)
```

**Database Resilience:**
- **Connection pooling** with automatic reconnection
- **WAL mode** for better concurrent access
- **Regular backups** with point-in-time recovery
- **Schema migration** system for updates
- **Transaction isolation** for data consistency

**System-level Resilience:**
- **Health check endpoints** for monitoring
- **Circuit breaker pattern** for external dependencies
- **Graceful shutdown** handling with job completion
- **Resource monitoring** (memory, CPU, disk)
- **Log aggregation** with structured JSON logging
- **Distributed tracing** with OpenTelemetry spans

### 6. Data Persistence

**SQLite Database Schema:**
```sql
-- Games table with indexes for efficient queries
CREATE TABLE games (
    game_id TEXT PRIMARY KEY,
    date DATETIME NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    venue TEXT,
    gameday_url TEXT,
    notification_sent BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_date (date),
    INDEX idx_notification (notification_sent, date)
);

-- Users table for subscription management
CREATE TABLE users (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    subscribed BOOLEAN DEFAULT TRUE,
    timezone TEXT DEFAULT 'America/Los_Angeles',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 7. Deployment & Operations Strategy

**Production Architecture:**
```yaml
# docker-compose.yml for single-server deployment
version: '3.8'
services:
  mariners-bot:
    build: .
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - MLB_API_KEY=${MLB_API_KEY}
      - DATABASE_URL=sqlite:///data/mariners_bot.db
      - LOG_LEVEL=INFO
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    ports:
      - "8000:8000"  # Health check endpoint
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

**Infrastructure Requirements:**
- **Minimum**: 1GB RAM, 1 vCPU, 10GB storage
- **Recommended**: 2GB RAM, 2 vCPU, 20GB storage (for growth)
- **Network**: Reliable internet with low latency to Telegram servers
- **Backup**: Daily automated backups to cloud storage

**Security Considerations:**
- **Token management**: Environment variables, never in code
- **HTTPS only**: All external communications encrypted
- **Input validation**: Sanitize all user inputs
- **Rate limiting**: Prevent abuse and spam
- **Regular updates**: Keep dependencies current

### 8. Monitoring & Observability

**OpenTelemetry Integration:**
```python
# OpenTelemetry setup
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

# Initialize providers
trace.set_tracer_provider(TracerProvider())
metrics.set_meter_provider(MeterProvider())

# Configure exporters (can export to Jaeger, Zipkin, or OTLP endpoint)
tracer_provider = trace.get_tracer_provider()
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317"))
)

# Auto-instrument libraries
AioHttpClientInstrumentor().instrument()
SQLAlchemyInstrumentor().instrument()

# Application metrics
meter = metrics.get_meter("mariners-bot")
tracer = trace.get_tracer("mariners-bot")

notifications_sent = meter.create_counter(
    "notifications_sent_total",
    description="Total notifications sent"
)
notification_latency = meter.create_histogram(
    "notification_latency_seconds", 
    description="Notification latency in seconds"
)
active_subscribers = meter.create_up_down_counter(
    "active_subscribers",
    description="Number of active subscribers"
)
mlb_api_calls = meter.create_counter(
    "mlb_api_calls_total",
    description="Total MLB API calls made"
)
```

**Tracing Implementation:**
```python
# Example of adding tracing to key functions
async def send_game_notification(game_id: str):
    with tracer.start_as_current_span("send_notification") as span:
        span.set_attribute("game.id", game_id)
        span.set_attribute("game.team", "seattle-mariners")
        
        try:
            # Fetch game details
            with tracer.start_as_current_span("fetch_game_details"):
                game = await get_game_details(game_id)
                span.set_attribute("game.opponent", game.away_team)
                
            # Send notification
            with tracer.start_as_current_span("telegram_send"):
                result = await bot.send_message(chat_id, message)
                notifications_sent.add(1, {"status": "success"})
                span.set_attribute("notification.success", True)
                
        except Exception as e:
            span.set_attribute("notification.success", False)
            span.set_attribute("error.message", str(e))
            notifications_sent.add(1, {"status": "error"})
            raise
```

**Observability Stack Options:**
1. **Local Development**: Jaeger for tracing, simple console exporters
2. **Self-hosted**: Jaeger + Prometheus + Grafana stack
3. **Cloud Solutions**: 
   - **Honeycomb**: Excellent for distributed tracing analysis
   - **DataDog**: Full observability platform
   - **New Relic**: APM with OpenTelemetry support
   - **Grafana Cloud**: Free tier with OTLP support

**Key Metrics to Monitor:**
- **Notification success rate**: Track delivery failures
- **API response times**: MLB API and Telegram API latency
- **Scheduled job execution**: Timing accuracy and delays
- **Database performance**: Query times and connection health
- **Memory and CPU usage**: Resource utilization trends

### 9. GitHub Actions CI/CD Pipeline

**Pipeline Stages:**
1. **Code Quality**: Linting, type checking, security scanning
2. **Testing**: Unit tests, integration tests, coverage reporting
3. **Build**: Multi-stage Docker image build with optimization
4. **Security**: Container vulnerability scanning
5. **Publish**: Push to GitHub Container Registry (ghcr.io)
6. **Deploy**: Automated deployment triggers (optional)

**Workflow Configuration:**

**`.github/workflows/ci-cd.yml`:**
```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [ main, develop ]
    tags: [ 'v*' ]
  pull_request:
    branches: [ main ]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
          
      - name: Install uv
        uses: astral-sh/setup-uv@v2
        
      - name: Install dependencies
        run: uv sync
        
      - name: Run linting
        run: |
          uv run ruff check .
          uv run mypy .
          
      - name: Run tests
        run: uv run pytest --cov=. --cov-report=html

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install uv
        uses: astral-sh/setup-uv@v2
        
      - name: Install dependencies
        run: uv sync
        
      - name: Run Bandit security scan
        run: uv run bandit -r . -f json -o bandit-report.json

  build-and-push:
    needs: [test, security]
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
      
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
        
      - name: Log in to Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
          
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest,enable={{is_default_branch}}
            
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          
      - name: Run Trivy vulnerability scanner
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
          format: 'sarif'
          output: 'trivy-results.sarif'
          
      - name: Upload Trivy scan results
        uses: github/codeql-action/upload-sarif@v2
        with:
          sarif_file: 'trivy-results.sarif'

  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: production
    
    steps:
      - name: Deploy to production
        run: |
          echo "Trigger deployment webhook or update infrastructure"
          # Could integrate with:
          # - Webhook to your server
          # - Terraform Cloud
          # - Kubernetes deployment
          # - Docker Compose update
```

**Optimized Multi-Stage Dockerfile:**
```dockerfile
# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Production stage
FROM python:3.11-slim as production

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Add venv to path
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY . .

# Change ownership to appuser
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Create data directory
RUN mkdir -p /app/data /app/logs

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose port
EXPOSE 8000

# Run application
CMD ["python", "-m", "mariners_bot.main"]
```

**Package Management:**

GitHub Container Registry benefits:
- Free for public repositories
- Integrated with GitHub Actions
- Automatic vulnerability scanning
- Multi-architecture support (AMD64/ARM64)
- Layer caching for faster builds

Image naming convention:
```
ghcr.io/yourusername/gameday-poster-bot:latest
ghcr.io/yourusername/gameday-poster-bot:v1.2.3
ghcr.io/yourusername/gameday-poster-bot:main
```

## Technology Stack

**Core Dependencies:**
```toml
# pyproject.toml
[project]
name = "mariners-bot"
version = "0.1.0"
description = "Seattle Mariners Gameday Telegram Bot"
requires-python = ">=3.11"
dependencies = [
    "python-telegram-bot>=20.7",
    "aiohttp>=3.9",
    "apscheduler>=3.10",
    "sqlalchemy>=2.0",
    "alembic>=1.12",
    "pydantic>=2.5",
    "pytz>=2023.3",
    "tenacity>=8.2",
    "opentelemetry-api>=1.36",
    "opentelemetry-sdk>=1.36",
    "opentelemetry-exporter-otlp>=1.36",
    "opentelemetry-instrumentation-aiohttp-client>=0.57b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.57b0",
    "structlog>=23.2",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "pytest-asyncio>=0.21",
    "ruff>=0.1",
    "mypy>=1.7",
    "bandit>=1.7",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=7.0",
    "pytest-cov>=4.0", 
    "pytest-asyncio>=0.21",
    "ruff>=0.1",
    "mypy>=1.7",
    "bandit>=1.7",
]
```

## Implementation Phases

### Phase 1: MVP (1-2 weeks)
- ✅ Basic Telegram bot setup
- ✅ MLB schedule fetching
- ✅ Simple notification scheduling
- ✅ SQLite data storage

### Phase 2: Production Ready (1-2 weeks)  
- ✅ Error handling and resilience
- ✅ Proper logging and monitoring
- ✅ Docker containerization
- ✅ CI/CD pipeline

### Phase 3: Enhanced Features (Optional)
- 📈 User subscription management
- 📊 Game prediction integration
- 🔔 Multiple notification types
- 📱 Web dashboard for management

## Expected Challenges & Solutions

1. **Timezone Complexity**: Use `pytz` library and store all times in UTC
2. **Schedule Changes**: Implement differential updates and conflict resolution
3. **Rate Limiting**: Implement proper backoff and queue management
4. **Game Postponements**: Monitor schedule changes and update notifications
5. **Server Reliability**: Use health checks, auto-restart, and monitoring

## System Architecture Diagram

```
[MLB API] → [Schedule Fetcher] → [Game Database] → [Scheduler] → [Telegram Bot] → [Users]
     ↑              ↓                    ↓              ↓              ↓
[Daily Sync]   [Parse Games]      [Store Games]   [Queue Jobs]   [Send Messages]
```

This architecture provides a robust, scalable foundation for the Mariners notification bot while maintaining simplicity for a personal project. The modular design allows for easy testing, debugging, and future enhancements.

## Key Strengths

- **Reliability**: Multiple layers of error handling and monitoring
- **Accuracy**: Timezone-aware scheduling with DST handling  
- **Maintainability**: Clean architecture with proper separation of concerns
- **Scalability**: Can easily handle multiple users and teams if needed
- **Observability**: Comprehensive logging and metrics for debugging
- **Security**: Container scanning, dependency checks, and secure deployment
- **Automation**: Full CI/CD pipeline with GitHub Actions integration
