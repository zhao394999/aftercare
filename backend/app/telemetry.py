from functools import wraps
from opentelemetry import propagate, trace
from .config import settings

SENSITIVE_TRACE_URLS = r"/api/v1/invitations/[^/?]+"
WORKER_HEARTBEAT_SECONDS = 90


def configure(service_name):
    cfg = settings()
    if not cfg.otel_exporter_otlp_endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=cfg.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
            )
        )
    )
    trace.set_tracer_provider(provider)
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry import metrics

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=cfg.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/metrics"
        )
    )
    metrics.set_meter_provider(
        MeterProvider(
            resource=Resource.create({"service.name": service_name}),
            metric_readers=[reader],
        )
    )
    meter = metrics.get_meter("aftercare.background")
    meter.create_observable_gauge("aftercare.jobs", callbacks=[job_metrics])
    meter.create_observable_gauge("aftercare.worker.alive", callbacks=[worker_metrics])
    meter.create_observable_gauge(
        "aftercare.jobs.oldest_pending_age", unit="s", callbacks=[queue_age]
    )


def job_metrics(_):
    from opentelemetry.metrics import Observation
    from sqlalchemy import func, select
    from .database import SessionLocal
    from .models import Job, now

    with SessionLocal() as db:
        counts = dict(
            db.execute(select(Job.state, func.count()).group_by(Job.state)).all()
        )
        expired = db.scalar(
            select(func.count(Job.id)).where(
                Job.state == "running", Job.lease_until < now()
            )
        )
    return [
        Observation(counts.get(state, 0), {"state": state})
        for state in ["pending", "running", "failed"]
    ] + [Observation(expired, {"state": "lease_expired"})]


def worker_metrics(_):
    from datetime import timedelta
    from opentelemetry.metrics import Observation
    from sqlalchemy import func, select
    from .database import SessionLocal
    from .models import WorkerHeartbeat, now

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(WorkerHeartbeat)
            .where(
                WorkerHeartbeat.seen_at
                > now() - timedelta(seconds=WORKER_HEARTBEAT_SECONDS)
            )
        )
    return [Observation(count)]


def queue_age(_):
    from opentelemetry.metrics import Observation
    from sqlalchemy import func, select
    from .database import SessionLocal
    from .models import Job, now

    with SessionLocal() as db:
        oldest = db.scalar(
            select(func.min(Job.created_at)).where(Job.state == "pending")
        )
    return [Observation(max(0, (now() - oldest).total_seconds()) if oldest else 0)]


def trace_context():
    carrier = {}
    propagate.inject(carrier)
    return carrier


def traced_node(name, fn):
    @wraps(fn)
    def wrapped(state):
        with trace.get_tracer("aftercare.agent").start_as_current_span(
            "agent." + name, record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("aftercare.run_id", state["run_id"])
            span.set_attribute("aftercare.tenant_id", state["tenant_id"])
            if name == "tool":
                span.set_attribute(
                    "aftercare.tool", state.get("decision", {}).get("tool", "")
                )
            try:
                return fn(state)
            except Exception as exc:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(
                    Status(StatusCode.ERROR, getattr(exc, "code", "internal_error"))
                )
                raise

    return wrapped


def record_request(route, status, duration):
    from opentelemetry import metrics

    meter = metrics.get_meter("aftercare.http")
    attrs = {"http.route": route, "http.response.status_code": status}
    meter.create_counter("aftercare.http.requests").add(1, attrs)
    meter.create_histogram("aftercare.http.duration", unit="ms").record(duration, attrs)
