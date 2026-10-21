import signal
import time
import logging
import json
from datetime import timedelta

from sqlalchemy import or_, select, update

from . import domain as d
from .agent import process_agent
from .database import SessionLocal, tenant_session, set_tenant, record_worker_heartbeat
from .models import AgentRun, ImportJob, Job, Order, WorkerHeartbeat, now, uid
from .schemas import OrderInput

stopping = False


def heartbeat():
    with SessionLocal.begin() as db:
        record_worker_heartbeat(db)
        db.query(WorkerHeartbeat).filter(
            WorkerHeartbeat.seen_at < now() - timedelta(days=1)
        ).delete()


def fail_resource(db, job, code):
    set_tenant(db, job.tenant_id)
    if job.kind == "agent":
        run = db.scalar(
            select(AgentRun)
            .where(AgentRun.id == job.resource_id, AgentRun.tenant_id == job.tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run and run.state not in {
            "cancelled",
            "completed",
            "human_needed",
            "waiting_input",
        }:
            run.state = "human_needed"
            run.error = code
            run.active_key = None
            run.completed_at = now()
            d.event(db, run, "run.human_needed", {"error": code})
    elif job.kind == "import":
        imp = db.scalar(
            select(ImportJob)
            .where(
                ImportJob.id == job.resource_id, ImportJob.tenant_id == job.tenant_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if imp and imp.state != "completed":
            imp.state = "failed"


def claim_job(job_id=None):
    with SessionLocal.begin() as db:
        ready = or_(
            (Job.state == "pending") & (Job.available_at <= now()),
            (Job.state == "running") & (Job.lease_until < now()),
        )
        query = select(Job).where(ready)
        if job_id:
            query = query.where(Job.id == job_id)
        job = db.scalar(
            query.order_by(Job.created_at).with_for_update(skip_locked=True).limit(1)
        )
        if not job:
            return None
        if job.attempts >= 3:
            job.state = "failed"
            job.lease_until = None
            job.last_error = "worker_crash_limit"
            fail_resource(db, job, job.last_error)
            return None
        token = uid()
        result = db.execute(
            update(Job)
            .where(
                Job.id == job.id,
                Job.state == job.state,
                Job.lease_token == job.lease_token,
            )
            .values(
                state="running",
                lease_token=token,
                lease_until=now() + timedelta(seconds=90),
                attempts=job.attempts + 1,
            )
        )
        if result.rowcount != 1:
            return None
        db.flush()
        db.refresh(job)
        db.expunge(job)
        return job, token


def process_import(job, token):
    while True:
        with tenant_session(job.tenant_id) as db:
            locked = db.scalar(
                select(Job)
                .where(
                    Job.id == job.id,
                    Job.lease_token == token,
                    Job.lease_until > now(),
                    Job.state == "running",
                )
                .with_for_update()
            )
            if not locked:
                return
            locked.lease_until = now() + timedelta(seconds=90)
            record_worker_heartbeat(db)
            imp = d.scoped(db, ImportJob, job.resource_id, job.tenant_id, lock=True)
            offset = imp.inserted + imp.skipped
            batch = imp.rows[offset : offset + 200]
            if not batch:
                imp.state = "completed"
                return
            imp.state = "running"
            for raw in batch:
                row = OrderInput.model_validate(raw)
                exists = db.scalar(
                    select(Order.id).where(
                        Order.tenant_id == job.tenant_id,
                        Order.channel == row.channel,
                        Order.external_id == row.external_id,
                    )
                )
                if exists:
                    imp.skipped += 1
                    continue
                values = row.model_dump()
                for key in ["placed_at", "shipped_at", "promised_at"]:
                    if values[key]:
                        values[key] = d.utc(values[key])
                db.add(Order(tenant_id=job.tenant_id, **values))
                imp.inserted += 1


def execute(job, token):
    try:
        if job.kind == "import":
            process_import(job, token)
        elif job.kind == "agent":
            process_agent(job, token)
        else:
            raise ValueError("unknown_job_kind")
        with SessionLocal.begin() as db:
            db.execute(
                update(Job)
                .where(Job.id == job.id, Job.lease_token == token)
                .values(state="done", lease_until=None)
            )
    except Exception as exc:
        code = getattr(exc, "code", "worker_failure")
        logging.getLogger("aftercare").error(
            json.dumps(
                {
                    "event": "job.failed",
                    "job_id": job.id,
                    "code": code,
                    "error_type": type(exc).__name__,
                }
            )
        )
        with SessionLocal.begin() as db:
            current = db.scalar(
                select(Job)
                .where(Job.id == job.id, Job.lease_token == token)
                .with_for_update()
            )
            if not current:
                return
            current.last_error = code
            retry = current.attempts < 3 and code not in {
                "agent_budget",
                "actor_revoked",
                "agent_paused",
                "run_stopped",
                "tenant_budget",
                "total_budget",
                "model_configuration_changed",
                "run_deadline",
            }
            current.state = "pending" if retry else "failed"
            current.available_at = now() + timedelta(
                seconds=min(60, 2**current.attempts)
            )
            current.lease_until = None
            if not retry:
                fail_resource(db, current, code)


def run_once(job_id=None):
    claimed = claim_job(job_id)
    if claimed:
        execute(*claimed)
    return bool(claimed)


def shutdown(*_):
    global stopping
    stopping = True


def main():
    from .telemetry import configure

    configure("aftercare-worker")
    from .database import verify_runtime_database

    verify_runtime_database()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print("Aftercare worker ready; database outbox polling enabled.", flush=True)
    while not stopping:
        heartbeat()
        if not run_once():
            time.sleep(1)


if __name__ == "__main__":
    main()
