# Single stage build
FROM python:3.11-slim

# Create non-root user with same UID/GID as host user (1000:1000)
RUN groupadd -g 1000 appuser && useradd -u 1000 -g 1000 -m appuser

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy application code
COPY . .

# Create virtual environment and install everything
RUN uv sync --frozen --no-dev

# Add venv to path
ENV PATH="/app/.venv/bin:$PATH"

# Create data and logs directories with proper permissions
RUN mkdir -p /app/data /app/logs

# Change ownership to appuser
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose port
EXPOSE 8000

# Run application
CMD ["mariners-bot", "start"]
