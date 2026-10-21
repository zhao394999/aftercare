from contextlib import contextmanager
from pathlib import Path
import os
import socket

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

cfg = settings()
if cfg.database_url.startswith("sqlite"):
    Path("runtime").mkdir(exist_ok=True)
engine = create_engine(
    cfg.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 20}
    if cfg.database_url.startswith("sqlite")
    else {
        "connect_timeout": 5,
        "options": "-c statement_timeout=15000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000",
    },
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def sqlite_options(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")


def set_tenant(db: Session, tenant_id: str):
    if engine.dialect.name == "postgresql":
        db.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_id}
        )
    db.info["tenant_id"] = tenant_id


def record_worker_heartbeat(db: Session):
    from .models import WorkerHeartbeat, now

    db.merge(
        WorkerHeartbeat(
            worker_id=f"{socket.gethostname()}:{os.getpid()}", seen_at=now()
        )
    )


@contextmanager
def tenant_session(tenant_id: str):
    with SessionLocal() as db:
        set_tenant(db, tenant_id)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def verify_runtime_database():
    """Called by every production API and worker process, not just the web process."""
    cfg = settings()
    cfg.validate_runtime()
    if cfg.app_env != "production":
        return
    from .models import SCOPED_TABLES

    with engine.connect() as db:
        role = db.execute(
            text(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        ).first()
        if role.rolsuper or role.rolbypassrls:
            raise RuntimeError("Runtime role must not bypass tenant RLS")
        rows = db.execute(
            text(
                "SELECT relname,relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner)=current_user AS owned FROM pg_class WHERE relnamespace=current_schema()::regnamespace"
            )
        ).all()
        enforced = {
            r.relname
            for r in rows
            if r.relrowsecurity and r.relforcerowsecurity and not r.owned
        }
        if not set(SCOPED_TABLES) <= enforced:
            raise RuntimeError(
                "Runtime role must be non-owner with forced tenant row security"
            )
