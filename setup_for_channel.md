# Quick Channel Setup Guide

## Step 1: Create Bot and Get Token
1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Use `/newbot` command
3. Follow prompts to create your bot
4. Save the bot token

## Step 2: Add Bot to Your Channel
1. Add your bot to the friend group channel
2. Make it an admin with "Send Messages" permission
3. Send a test message mentioning the bot: `Hello @your_bot_name`

## Step 3: Get Channel Chat ID
```bash
# Run the helper script
uv run python get_chat_id.py
```

## Step 4: Configure Environment
```bash
# Copy template and edit
cp env.template .env

# Add your values:
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGhIjKlMnOpQrStUvWxYz
TELEGRAM_CHAT_ID=-1001234567890  # Your channel ID (starts with -100)
```

## Step 5: Start the Bot
```bash
# Initialize database
uv run mariners-bot init-db

# Start the bot
uv run mariners-bot start
```

## Expected Behavior
- Bot will send game notifications to your channel 5 minutes before each Mariners game
- Messages include opponent, venue, time, and direct Gameday link
- No need for individual subscriptions - everyone in the channel sees notifications

## Example Notification
```
🔥 Mariners Game Starting Soon!
⚾ Seattle Mariners vs Atlanta Braves
🏟️ Truist Park
📍 Playing away ✈️
🕐 Starts in 5 minutes (9:05 AM PT)
📺 Watch Live on MLB Gameday
```
