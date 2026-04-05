# Mariners Gameday Bot

Telegram bot that posts Seattle Mariners game notifications: a pre-game alert 5 minutes before first pitch, and a spoiler-tagged final score once the game ends.

## Dev commands

```bash
uv run mariners-bot start                        # Run the bot
uv run pytest tests/                             # Run tests
uv run ruff check --fix .                        # Lint (auto-fix)
uv run mypy mariners_bot/                        # Type check
uv run mariners-bot sync-schedule                # Manually fetch schedule
uv run mariners-bot migrate -m "describe change" # Generate a migration after editing models
```

Migrations are auto-generated — after changing a model, run `migrate` and Alembic diffs the schema and writes the file. Review the output in `alembic/versions/` before committing. The bot runs `alembic upgrade head` automatically on startup.

## Architecture

- **`mariners_bot/clients/mlb_client.py`** — MLB Stats API (base URL: `statsapi.mlb.com/api/v1`, Mariners team ID: `136`)
- **`mariners_bot/scheduler/game_scheduler.py`** — APScheduler jobs: pre-game notifications (DateTrigger) and final score poller (IntervalTrigger, every 30s)
- **`mariners_bot/scheduler/transaction_scheduler.py`** — Polls MLB transactions every 5 minutes; notifies users of trades, signings, injuries, etc. Per-user preferences stored in `user_transaction_preferences` table.
- **`mariners_bot/bot/telegram_bot.py`** — Telegram bot, command handlers, message sending
- **`mariners_bot/database/`** — SQLite via SQLAlchemy async; Pydantic models in `mariners_bot/models/`
- **`mariners_bot/main.py`** — Wires everything together; owns `_sync_schedule`, `_sync_transactions`, `_check_final_scores`

## Key gotchas

**APScheduler can't serialize instance methods.** All scheduler callbacks must be module-level async wrapper functions that call into a stored global callable. See the `_notification_callback` / `_notification_wrapper` pattern in `game_scheduler.py`.

**All datetimes are stored as UTC.** Convert to Pacific Time only for display. Use `pytz` (already used in scheduler) not `zoneinfo` for consistency.

**Final score polling only runs for games where `notification_sent=True` and `final_score_sent=False`** within the last 12 hours. Adding the `final_score_sent` column to an existing database requires:
```sql
ALTER TABLE games ADD COLUMN final_score_sent BOOLEAN DEFAULT FALSE;
```

**Telegram messages use HTML parse mode** with `disable_web_page_preview=True`. Use `<tg-spoiler>text</tg-spoiler>` for spoiler tags.

**Bot only responds to private chat messages** (not channel posts) unless it's a command.

## OpenTelemetry

Auto-instrumentation is set up for **aiohttp** (all MLB API calls) and **SQLAlchemy** (all DB queries) — spans for those layers are generated automatically with no manual code needed.

The only manual span in the codebase is `handle_next_game_command` in `telegram_bot.py:_handle_next_game`. When adding new spans, use `span.record_exception(e)` + `span.set_status(trace.StatusCode.ERROR, str(e))` for error cases — not `span.set_attribute("error", ...)`, which doesn't mark the span as errored in Honeycomb/backends.

The OTLP exporter uses **HTTP/protobuf** (`opentelemetry.exporter.otlp.proto.http`), so use an HTTP endpoint (Honeycomb: `https://api.honeycomb.io`). `OTEL_EXPORTER_OTLP_HEADERS` is parsed manually as `key=value,key2=value2` — if the string is set but no valid headers are parsed, a warning is logged.

Metrics instruments are defined in `observability.py:create_app_metrics()` but none are currently wired up with `.add()` / `.record()` calls — if you add instrumentation, that's where the instrument definitions live.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | No | Channel/chat ID for broadcast notifications |
| `DATABASE_URL` | No | SQLite URL (default: `sqlite:///data/mariners_bot.db`) |
| `SCHEDULER_TIMEZONE` | No | Display timezone (default: `America/Los_Angeles`) |
| `NOTIFICATION_ADVANCE_MINUTES` | No | Minutes before game to notify (default: `5`) |
| `SCHEDULE_SYNC_HOUR` | No | Hour (PT) to refresh schedule daily (default: `6`) |
| `OTEL_TRACES_EXPORTER` | No | `none`, `console`, or `otlp` (default: `none`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | OTLP endpoint URL (e.g. Honeycomb) |
| `OTEL_EXPORTER_OTLP_HEADERS` | No | Auth headers for OTLP (e.g. `x-honeycomb-team=...`) |
| `PLAYBYPLAY_CHANNEL_ID` | No | Telegram channel ID where per-inning header posts are sent; feature is inert when unset |
| `PLAYBYPLAY_GROUP_ID` | No | Linked discussion group ID where play-by-play replies are threaded under channel posts |
| `PLAYBYPLAY_CHANNEL_USERNAME` | No | Public `@username` of the channel, used to build `t.me/` deep links (falls back to numeric ID for private channels) |
| `PLAYBYPLAY_POLL_INTERVAL` | No | Seconds between MLB live feed polls during active games (default: `20`) |
| `PLAYBYPLAY_RETENTION_HOURS` | No | Hours to retain play-by-play DB data after a game ends before cleanup deletes it (default: `72`) |
