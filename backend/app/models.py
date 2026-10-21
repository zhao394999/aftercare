import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Scoped(Identity):
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)


class Tenant(Identity, Base):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(100))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class User(Identity, Base):
    __tablename__ = "auth_users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    password_hash: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Identity, Base):
    __tablename__ = "auth_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SessionRecord(Identity, Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    expires_at: Mapped[datetime]


class AuthToken(Identity, Base):
    __tablename__ = "auth_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    tenant_id: Mapped[str] = mapped_column(String(36))
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class LoginThrottle(Base):
    __tablename__ = "auth_login_throttle"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    window_at: Mapped[datetime] = mapped_column(default=now)


class Order(Scoped, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "external_id"),
        CheckConstraint(
            "paid_cents > 0 AND refunded_cents >= 0 AND reserved_cents >= 0 AND refunded_cents + reserved_cents <= paid_cents",
            name="ck_order_ledger",
        ),
    )
    external_id: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(50), default="商家导入")
    product: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(default=1)
    paid_cents: Mapped[int] = mapped_column(Integer)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    reserved_cents: Mapped[int] = mapped_column(Integer, default=0)
    customer: Mapped[str] = mapped_column(String(80), default="")
    carrier_code: Mapped[str] = mapped_column(String(50), default="")
    tracking_number: Mapped[str] = mapped_column(String(32), default="")
    tracking_phone: Mapped[str] = mapped_column(String(32), default="")
    logistics_queried_at: Mapped[datetime | None]
    logistics_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="shipped")
    placed_at: Mapped[datetime]
    shipped_at: Mapped[datetime | None]
    promised_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1)
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class ImportJob(Scoped, Base):
    __tablename__ = "import_jobs"
    filename: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    rows: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(30), default="preview")
    inserted: Mapped[int] = mapped_column(default=0)
    skipped: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[str] = mapped_column(String(36))


class Ticket(Scoped, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        Index("uq_ticket_source_ref", "tenant_id", "source_ref", unique=True),
    )
    source_ref: Mapped[str | None] = mapped_column(String(330))
    number: Mapped[str] = mapped_column(String(40), unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    request: Mapped[str] = mapped_column(Text)
    issue: Mapped[str] = mapped_column(String(40), default="logistics_delay")
    state: Mapped[str] = mapped_column(String(40), default="new", index=True)
    owner_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(default=1)
    evidence_version: Mapped[int] = mapped_column(default=0)
    due_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class Evidence(Scoped, Base):
    __tablename__ = "evidence"
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime]
    expires_at: Mapped[datetime | None]
    file_key: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(String(200))
    mime: Mapped[str | None] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[str | None] = mapped_column(String(36))
    uploaded_by: Mapped[str] = mapped_column(String(36))


class Policy(Scoped, Base):
    __tablename__ = "policies"
    title: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(default=1)
    state: Mapped[str] = mapped_column(String(30), default="draft")
    channel: Mapped[str] = mapped_column(String(50), default="*")
    body: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[datetime]
    effective_until: Mapped[datetime | None]
    allow_refund_on_loss: Mapped[bool] = mapped_column(Boolean, default=False)
    max_refund_cents: Mapped[int] = mapped_column(default=0)
    published_by: Mapped[str | None] = mapped_column(String(36))
    published_at: Mapped[datetime | None]
    content_hash: Mapped[str] = mapped_column(String(64))


class Proposal(Scoped, Base):
    __tablename__ = "proposals"
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(30), default="refund")
    amount_cents: Mapped[int]
    reason: Mapped[str] = mapped_column(Text)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"))
    policy_hash: Mapped[str] = mapped_column(String(64))
    evidence_ids: Mapped[list] = mapped_column(JSON)
    evidence_version: Mapped[int]
    order_version: Mapped[int]
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="pending")
    expires_at: Mapped[datetime]
    decided_by: Mapped[str | None] = mapped_column(String(36))
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str] = mapped_column(Text, default="")


class Action(Scoped, Base):
    __tablename__ = "action_executions"
    __table_args__ = (
        UniqueConstraint("proposal_id"),
        UniqueConstraint("tenant_id", "external_ref"),
    )
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount_cents: Mapped[int]
    state: Mapped[str] = mapped_column(String(40), default="approved")
    claimed_by: Mapped[str | None] = mapped_column(String(36))
    claimed_at: Mapped[datetime | None]
    checked_at: Mapped[datetime | None]
    external_ref: Mapped[str | None] = mapped_column(String(160))
    external_state: Mapped[str | None] = mapped_column(String(40))
    actual_cents: Mapped[int | None]
    receipt_evidence_id: Mapped[str | None] = mapped_column(String(36))
    executed_by: Mapped[str | None] = mapped_column(String(36))
    executed_at: Mapped[datetime | None]
    verified_by: Mapped[str | None] = mapped_column(String(36))
    verified_at: Mapped[datetime | None]
    note: Mapped[str] = mapped_column(Text, default="")


class Communication(Scoped, Base):
    __tablename__ = "communications"
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"))
    channel: Mapped[str] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(36))


class AuditEvent(Scoped, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[str] = mapped_column(String(36))
    event: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    request_id: Mapped[str] = mapped_column(String(64), default="")


class AgentRun(Scoped, Base):
    __tablename__ = "agent_runs"
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"), index=True)
    state: Mapped[str] = mapped_column(String(40), default="queued")
    requester_id: Mapped[str] = mapped_column(String(36))
    active_key: Mapped[str | None] = mapped_column(String(36), unique=True)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    call_count: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_microusd: Mapped[int] = mapped_column(default=0)
    reserved_microusd: Mapped[int] = mapped_column(default=0)
    budget_microusd: Mapped[int]
    model: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(30), default="planner-v1")
    error: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None]


class RunEvent(Base):
    __tablename__ = "run_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)


class ModelCall(Scoped, Base):
    __tablename__ = "model_calls"
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default="started")
    context: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_microusd: Mapped[int] = mapped_column(default=0)
    reserved_microusd: Mapped[int]
    pricing: Mapped[dict] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class Job(Identity, Base):
    """Outbox/dispatcher metadata only; no customer or model payload."""

    __tablename__ = "jobs"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None]
    available_at: Mapped[datetime] = mapped_column(default=now)
    dispatched_at: Mapped[datetime | None]
    last_error: Mapped[str] = mapped_column(Text, default="")


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(default=now)


class ModelBudget(Base):
    __tablename__ = "model_budget"
    __table_args__ = (CheckConstraint("spent_microusd >= 0 AND reserved_microusd >= 0", name="ck_model_budget"),)
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    spent_microusd: Mapped[int] = mapped_column(default=0)
    reserved_microusd: Mapped[int] = mapped_column(default=0)


SCOPED_TABLES = [
    table.name
    for table in Base.metadata.sorted_tables
    if "tenant_id" in table.c
    and table.name not in {"auth_memberships", "auth_sessions", "auth_tokens", "jobs"}
]
Index("ix_ticket_tenant_state", Ticket.tenant_id, Ticket.state, Ticket.created_at)

Index("ix_jobs_dispatch", Job.state, Job.available_at, Job.lease_until)
