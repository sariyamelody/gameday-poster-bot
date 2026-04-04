"""OpenTelemetry observability setup and configuration."""

import logging

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
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
        f"Trace exporter: {settings.otel_traces_exporter}"
    )


def _parse_otlp_headers(headers_str: str) -> dict[str, str]:
    """Parse OTLP headers from 'key=value,key2=value2' format."""
    headers: dict[str, str] = {}
    for header in headers_str.split(","):
        if "=" in header:
            key, value = header.strip().split("=", 1)
            headers[key.strip()] = value.strip()
    if headers_str and not headers:
        logger.warning("OTEL_EXPORTER_OTLP_HEADERS was set but no valid headers parsed — expected 'key=value,key2=value2' format")
    return headers


def _setup_trace_exporters(tracer_provider: TracerProvider, settings: Settings) -> None:
    """Configure trace exporters based on settings."""

    exporters_added = 0

    # Console exporter — enabled by OTEL_TRACES_EXPORTER=console
    if settings.otel_traces_exporter == "console":
        console_exporter = ConsoleSpanExporter()
        tracer_provider.add_span_processor(SimpleSpanProcessor(console_exporter))
        logger.info("Added console trace exporter (stdout) — use only for local debugging, blocks on every span")
        exporters_added += 1

    # OTLP exporter (for Honeycomb, DataDog, New Relic, etc.)
    if settings.otel_traces_exporter == "otlp" or settings.otel_exporter_otlp_endpoint:
        if not settings.otel_exporter_otlp_endpoint:
            logger.warning("OTLP traces requested but OTEL_EXPORTER_OTLP_ENDPOINT is not configured")
        else:
            headers = _parse_otlp_headers(settings.otel_exporter_otlp_headers) if settings.otel_exporter_otlp_headers else {}
            try:
                otlp_exporter = OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    headers=headers if headers else None,
                )
                tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                logger.info(f"Added OTLP trace exporter: {settings.otel_exporter_otlp_endpoint}")
                exporters_added += 1
            except Exception as e:
                _log_exporter_failure("trace", settings, e)

    if exporters_added == 0:
        logger.info("No trace exporters configured - tracing disabled")


def _setup_metric_readers(settings: Settings) -> list[MetricReader]:
    """Configure metric readers based on settings."""
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    readers: list[MetricReader] = []

    if settings.otel_exporter_otlp_endpoint:
        headers = _parse_otlp_headers(settings.otel_exporter_otlp_headers) if settings.otel_exporter_otlp_headers else {}
        try:
            otlp_metric_exporter = OTLPMetricExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                headers=headers if headers else None,
            )
            metric_reader = PeriodicExportingMetricReader(
                exporter=otlp_metric_exporter,
                export_interval_millis=30000,
            )
            readers.append(metric_reader)
            logger.info(f"Added OTLP metric exporter: {settings.otel_exporter_otlp_endpoint}")
        except Exception as e:
            _log_exporter_failure("metric", settings, e)

    return readers


def _log_exporter_failure(exporter_type: str, settings: Settings, e: Exception) -> None:
    """Log exporter setup failure — error in production, warning elsewhere."""
    msg = f"Failed to setup OTLP {exporter_type} exporter: {e}"
    if settings.environment == "production":
        logger.error(msg)
    else:
        logger.warning(msg)


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


def shutdown_telemetry() -> None:
    """Flush and shut down the tracer and meter providers."""
    from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
    from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider

    try:
        tracer_provider = trace.get_tracer_provider()
        if isinstance(tracer_provider, SdkTracerProvider):
            tracer_provider.force_flush()
            tracer_provider.shutdown()
    except Exception as e:
        logger.warning(f"Error shutting down tracer provider: {e}")

    try:
        meter_provider = metrics.get_meter_provider()
        if isinstance(meter_provider, SdkMeterProvider):
            meter_provider.force_flush()
            meter_provider.shutdown()
    except Exception as e:
        logger.warning(f"Error shutting down meter provider: {e}")


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
