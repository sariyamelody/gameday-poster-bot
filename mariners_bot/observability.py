"""OpenTelemetry observability setup and configuration."""

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from mariners_bot.config import Settings

logger = logging.getLogger(__name__)


def setup_telemetry(settings: Settings) -> None:
    """Set up OpenTelemetry tracing and metrics based on configuration."""

    # Create resource with service information
    resource = Resource.create({
        "service.name": settings.otel_service_name,
        "service.version": "0.1.0",
        "deployment.environment": settings.environment,
    })

    # Set up tracing
    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)

    # Configure trace exporters based on settings
    _setup_trace_exporters(tracer_provider, settings)

    # Set up metrics with readers
    metric_readers = _setup_metric_readers(settings)
    metric_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    metrics.set_meter_provider(metric_provider)

    # Auto-instrument libraries
    _setup_auto_instrumentation()

    logger.info(
        f"OpenTelemetry initialized - Service: {settings.otel_service_name}, "
        f"Environment: {settings.environment}, "
        f"Trace exporter: {settings.otel_traces_exporter}, "
        f"Stdout traces: {settings.otel_traces_to_stdout}"
    )


def _setup_trace_exporters(tracer_provider: TracerProvider, settings: Settings) -> None:
    """Configure trace exporters based on settings."""

    exporters_added = 0

    # Console/stdout exporter
    if settings.otel_traces_to_stdout or settings.otel_traces_exporter == "console":
        console_exporter = ConsoleSpanExporter()
        # Use SimpleSpanProcessor for console output for immediate visibility
        tracer_provider.add_span_processor(SimpleSpanProcessor(console_exporter))
        logger.info("Added console trace exporter (stdout)")
        exporters_added += 1

    # OTLP exporter (for Honeycomb, DataDog, New Relic, etc.)
    if (settings.otel_traces_exporter == "otlp" or
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")):

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            logger.info("OTLP traces requested but no OTEL_EXPORTER_OTLP_ENDPOINT configured")
        else:
            # Get headers for authentication (e.g., Honeycomb API key)
            headers_str = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
            headers = {}
            if headers_str:
                # Parse headers like "key1=value1,key2=value2"
                for header in headers_str.split(","):
                    if "=" in header:
                        key, value = header.strip().split("=", 1)
                        headers[key.strip()] = value.strip()

            try:
                # Use headers if provided (for Honeycomb, DataDog, etc.)
                if headers:
                    otlp_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
                    logger.info(f"Added OTLP trace exporter with auth: {endpoint}")
                else:
                    otlp_exporter = OTLPSpanExporter(endpoint=endpoint)
                    logger.info(f"Added OTLP trace exporter: {endpoint}")

                tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                exporters_added += 1
            except Exception as e:
                logger.warning(f"Failed to setup OTLP trace exporter: {e}")


    if exporters_added == 0:
        logger.info("No trace exporters configured - tracing disabled")


def _setup_metric_readers(_settings: Settings) -> list[MetricReader]:
    """Configure metric readers based on settings."""

    readers: list[MetricReader] = []

    # OTLP metrics exporter (for Honeycomb, etc.)
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

        # Get headers for authentication
        headers_str = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
        headers = {}
        if headers_str:
            for header in headers_str.split(","):
                if "=" in header:
                    key, value = header.strip().split("=", 1)
                    headers[key.strip()] = value.strip()

        try:
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            # Use headers if provided
            if headers:
                otlp_metric_exporter = OTLPMetricExporter(endpoint=endpoint, headers=headers)
                logger.info(f"Added OTLP metric exporter with auth: {endpoint}")
            else:
                otlp_metric_exporter = OTLPMetricExporter(endpoint=endpoint)
                logger.info(f"Added OTLP metric exporter: {endpoint}")

            metric_reader = PeriodicExportingMetricReader(
                exporter=otlp_metric_exporter,
                export_interval_millis=30000,  # Export every 30 seconds
            )
            readers.append(metric_reader)
        except Exception as e:
            logger.warning(f"Failed to setup OTLP metric exporter: {e}")

    return readers


def _setup_auto_instrumentation() -> None:
    """Set up automatic instrumentation for supported libraries."""

    try:
        # Instrument aiohttp client for MLB API calls
        AioHttpClientInstrumentor().instrument()
        logger.info("Instrumented aiohttp client")
    except Exception as e:
        logger.warning(f"Failed to instrument aiohttp: {e}")

    try:
        # Instrument SQLAlchemy for database operations
        SQLAlchemyInstrumentor().instrument()
        logger.info("Instrumented SQLAlchemy")
    except Exception as e:
        logger.warning(f"Failed to instrument SQLAlchemy: {e}")


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer for the specified component."""
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    """Get a meter for the specified component."""
    return metrics.get_meter(name)


# Application-specific metrics
def create_app_metrics() -> dict[str, metrics.Instrument]:
    """Create application-specific metrics."""
    meter = get_meter("mariners-bot")

    return {
        "notifications_sent": meter.create_counter(
            "notifications_sent_total",
            description="Total notifications sent",
            unit="1"
        ),
        "notification_latency": meter.create_histogram(
            "notification_latency_seconds",
            description="Notification latency in seconds",
            unit="s"
        ),
        "active_subscribers": meter.create_up_down_counter(
            "active_subscribers",
            description="Number of active subscribers",
            unit="1"
        ),
        "mlb_api_calls": meter.create_counter(
            "mlb_api_calls_total",
            description="Total MLB API calls made",
            unit="1"
        ),
        "games_processed": meter.create_counter(
            "games_processed_total",
            description="Total games processed for scheduling",
            unit="1"
        ),
        "scheduler_jobs": meter.create_up_down_counter(
            "scheduler_jobs_active",
            description="Number of active scheduled jobs",
            unit="1"
        ),
    }
