from celery import Celery
from datetime import timedelta
from sqlalchemy import or_, select

from .config import settings
from .database import SessionLocal
from .models import Job, now
from .worker import run_once, heartbeat

celery = Celery("aftercare", broker=settings().redis_url or "redis://127.0.0.1:6379/0")
celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_soft_time_limit=600,
    task_time_limit=620,
    beat_schedule={"dispatch-outbox": {"task": "aftercare.dispatch", "schedule": 2.0}},
)


@celery.task(name="aftercare.process")
def process(job_id: str):
    run_once(job_id)


@celery.task(name="aftercare.dispatch")
def dispatch():
    with SessionLocal.begin() as db:
        jobs = list(
            db.scalars(
                select(Job)
                .where(
                    or_(
                        (Job.state == "pending") & (Job.available_at <= now()),
                        (Job.state == "running") & (Job.lease_until < now()),
                    )
                )
                .where(
                    or_(
                        Job.dispatched_at.is_(None),
                        Job.dispatched_at < now() - timedelta(seconds=30),
                    )
                )
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        )
        for job in jobs:
            process.delay(job.id)
            # The outbox retries delivery if Redis loses a message.
            job.dispatched_at = now()


from celery.signals import worker_process_init, heartbeat_sent


@heartbeat_sent.connect
def worker_heartbeat(**_):
    try:
        heartbeat()
    except Exception:
        import logging

        logging.getLogger("aftercare").error("worker_heartbeat_failed")


@worker_process_init.connect
def init_telemetry(**_):
    from .telemetry import configure

    configure("aftercare-celery-worker")
    from .database import verify_runtime_database

    try:
        verify_runtime_database()
    except Exception:
        raise SystemExit(78)
