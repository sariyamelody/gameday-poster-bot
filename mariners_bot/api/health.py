"""Health check API endpoints."""

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_serializer

from ..config import get_settings
from ..database import get_database_session

logger = structlog.get_logger(__name__)


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str
    timestamp: datetime
    version: str
    environment: str
    checks: dict[str, Any]

    @field_serializer('timestamp')
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize datetime to ISO format."""
        return value.isoformat()


class HealthCheckApp:
    """FastAPI application for health checks."""

    def __init__(self):
        """Initialize the health check app."""
        self.settings = get_settings()
        self.app = FastAPI(
            title="Mariners Bot Health Check",
            description="Health monitoring endpoint for the Seattle Mariners Gameday Bot",
            version="0.1.0",
            docs_url="/docs" if self.settings.debug else None,
            redoc_url="/redoc" if self.settings.debug else None,
        )

        # Add routes
        self.app.get("/health", response_model=HealthResponse)(self.health_check)
        self.app.get("/", response_model=HealthResponse)(self.health_check)

    async def health_check(self) -> HealthResponse:
        """Comprehensive health check endpoint."""
        checks = {}
        overall_status = "healthy"

        try:
            # Database connectivity check
            db_status = await self._check_database()
            checks["database"] = db_status
            if not db_status["healthy"]:
                overall_status = "unhealthy"

            # Memory and basic system checks
            checks["system"] = self._check_system()

            # Configuration validation
            checks["configuration"] = self._check_configuration()
            if not checks["configuration"]["healthy"]:
                overall_status = "degraded"

        except Exception as e:
            logger.error("Health check failed", error=str(e))
            overall_status = "unhealthy"
            checks["error"] = str(e)

        response = HealthResponse(
            status=overall_status,
            timestamp=datetime.now(UTC),
            version="0.1.0",
            environment=self.settings.environment,
            checks=checks
        )

        # Return appropriate HTTP status
        if overall_status == "unhealthy":
            raise HTTPException(status_code=503, detail=response.model_dump())

        return response

    async def _check_database(self) -> dict[str, Any]:
        """Check database connectivity and basic operations."""
        try:
            db_session = get_database_session(self.settings)

            # Test basic connectivity with a simple query
            start_time = datetime.now(UTC)

            # Use raw SQL for a simple connectivity test
            async with db_session.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text("SELECT 1"))
                result.fetchone()  # fetchone() is not awaitable

            end_time = datetime.now(UTC)
            latency_ms = (end_time - start_time).total_seconds() * 1000

            await db_session.close()

            return {
                "healthy": True,
                "latency_ms": round(latency_ms, 2),
                "database_url": self.settings.database_url.split("://")[0] + "://***",  # Hide credentials
                "status": "connected"
            }

        except Exception as e:
            logger.error("Database health check failed", error=str(e))
            return {
                "healthy": False,
                "error": str(e),
                "status": "disconnected"
            }

    def _check_system(self) -> dict[str, Any]:
        """Check basic system health."""
        try:
            import os

            import psutil

            # Get memory usage
            memory = psutil.virtual_memory()

            # Get current process info
            process = psutil.Process(os.getpid())

            return {
                "healthy": True,
                "memory": {
                    "total_mb": round(memory.total / 1024 / 1024, 2),
                    "available_mb": round(memory.available / 1024 / 1024, 2),
                    "percent_used": memory.percent,
                },
                "process": {
                    "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
                    "cpu_percent": process.cpu_percent(),
                    "pid": process.pid,
                },
                "uptime_seconds": round((datetime.now(UTC) - datetime.fromtimestamp(process.create_time(), UTC)).total_seconds(), 2)
            }

        except ImportError:
            # psutil not available, return basic info
            return {
                "healthy": True,
                "message": "Limited system info (psutil not available)",
                "basic_check": "passed"
            }
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e)
            }

    def _check_configuration(self) -> dict[str, Any]:
        """Check configuration validity."""
        issues = []

        # Check required configuration
        if not self.settings.telegram_bot_token:
            issues.append("telegram_bot_token not configured")

        if not self.settings.telegram_chat_id:
            issues.append("telegram_chat_id not configured")

        # Check if timezone is valid
        try:
            import pytz
            pytz.timezone(self.settings.scheduler_timezone)
        except Exception:
            issues.append(f"Invalid timezone: {self.settings.scheduler_timezone}")

        return {
            "healthy": len(issues) == 0,
            "issues": issues,
            "settings": {
                "log_level": self.settings.log_level,
                "environment": self.settings.environment,
                "debug": self.settings.debug,
                "scheduler_timezone": self.settings.scheduler_timezone,
                "health_check_port": self.settings.health_check_port,
            }
        }


def create_health_app() -> FastAPI:
    """Create and return the health check FastAPI application."""
    health_app = HealthCheckApp()
    return health_app.app
