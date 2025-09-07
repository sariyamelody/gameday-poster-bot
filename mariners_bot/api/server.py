"""Health check server for running the FastAPI application."""

import asyncio
import signal
import sys
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI

from ..config import get_settings
from .health import create_health_app

logger = structlog.get_logger(__name__)


class HealthServer:
    """Manages the health check HTTP server."""
    
    def __init__(self):
        """Initialize the health server."""
        self.settings = get_settings()
        self.app = create_health_app()
        self.server = None
        self.server_task = None
    
    async def start(self) -> None:
        """Start the health check server."""
        config = uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=self.settings.health_check_port,
            log_level=self.settings.log_level.lower(),
            access_log=self.settings.debug,
            loop="asyncio"
        )
        
        self.server = uvicorn.Server(config)
        
        # Run server in background task
        self.server_task = asyncio.create_task(self.server.serve())
        
        logger.info(
            "Health check server started",
            port=self.settings.health_check_port,
            endpoint=f"http://0.0.0.0:{self.settings.health_check_port}/health"
        )
    
    async def stop(self) -> None:
        """Stop the health check server."""
        if self.server:
            self.server.should_exit = True
            
        if self.server_task:
            try:
                await asyncio.wait_for(self.server_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Health server shutdown timed out")
                self.server_task.cancel()
                try:
                    await self.server_task
                except asyncio.CancelledError:
                    pass
        
        logger.info("Health check server stopped")


# Standalone health server for development/testing
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager."""
    logger.info("Health check API starting up")
    yield
    logger.info("Health check API shutting down")


def create_standalone_app() -> FastAPI:
    """Create standalone health check app with lifespan management."""
    health_app = create_health_app()
    
    # Add lifespan management
    health_app.router.lifespan_context = lifespan
    
    return health_app


async def run_health_server_standalone() -> None:
    """Run the health server as a standalone application."""
    settings = get_settings()
    
    # Setup signal handlers
    def signal_handler(signum, frame):
        logger.info("Received shutdown signal", signal=signum)
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and run server
    config = uvicorn.Config(
        create_standalone_app(),
        host="0.0.0.0",
        port=settings.health_check_port,
        log_level=settings.log_level.lower(),
        access_log=settings.debug,
    )
    
    server = uvicorn.Server(config)
    logger.info(f"Starting standalone health server on port {settings.health_check_port}")
    
    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("Health server stopped by user")
    except Exception as e:
        logger.error("Health server error", error=str(e))
        sys.exit(1)
