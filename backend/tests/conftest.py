import os
import tempfile
from datetime import timedelta

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(
    prefix="aftercare-test-", suffix=".db"
)
os.environ["APP_ENV"] = "development"
os.environ["DEMO_MODE"] = "false"
for field in ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"]:
    os.environ[field] = ""

import pytest
from fastapi.testclient import TestClient
from app.database import SessionLocal, engine, set_tenant
from app.main import app
from app.models import (
    Base,
    Evidence,
    Membership,
    Order,
    Policy,
    Tenant,
    Ticket,
    User,
    now,
)
from app.security import hasher
from app.config import settings

PASSWORD = "Test-fixture-only-Password-12"
HASH = hasher.hash(PASSWORD)
ORIGIN = "http://127.0.0.1:5173"


@pytest.fixture
def configured(monkeypatch):
    cfg = settings()
    for name, value in {
        "llm_base_url": "https://provider.test/v1",
        "llm_api_key": "test-only-key",
        "llm_model": "fixture-model",
        "llm_input_usd_per_million": 1.0,
        "llm_output_usd_per_million": 2.0,
        "llm_max_calls": 12,
        "llm_tenant_daily_budget_microusd": 10_000_000,
    }.items():
        monkeypatch.setattr(cfg, name, value)
    return cfg


@pytest.fixture
def world():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal.begin() as db:
        tenant = Tenant(name="测试商家", config={"approval_hours": 24})
        other = Tenant(name="其他商家", config={})
        db.add_all([tenant, other])
        db.flush()
        users = {}
        for role in ["staff", "supervisor", "executor", "admin"]:
            user = User(name=role, email=role + "@test.local", password_hash=HASH)
            db.add(user)
            db.flush()
            db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
            users[role] = user.id
        outsider = User(name="outside", email="outside@test.local", password_hash=HASH)
        db.add(outsider)
        db.flush()
        db.add(Membership(tenant_id=other.id, user_id=outsider.id, role="admin"))
        set_tenant(db, tenant.id)
        order = Order(
            tenant_id=tenant.id,
            external_id="ORDER-1",
            product="测试商品",
            paid_cents=12900,
            status="shipped",
            placed_at=now() - timedelta(days=5),
            verified_at=now(),
            verified_by=users["staff"],
        )
        policy = Policy(
            tenant_id=tenant.id,
            title="测试政策",
            body="确认丢件后可提出退款，必须人工审批。",
            state="published",
            effective_from=now() - timedelta(days=100),
            allow_refund_on_loss=True,
            max_refund_cents=100000,
            content_hash="a" * 64,
        )
        db.add_all([order, policy])
        db.flush()
        ticket = Ticket(
            tenant_id=tenant.id,
            order_id=order.id,
            number="TEST-1",
            title="物流异常退款",
            request="客户希望确认丢件后退款",
            owner_id=users["staff"],
            due_at=now() + timedelta(hours=24),
            evidence_version=1,
        )
        db.add(ticket)
        db.flush()
        evidence = Evidence(
            tenant_id=tenant.id,
            ticket_id=ticket.id,
            kind="carrier_confirmation",
            title="丢件证明",
            body="测试承运商确认丢件",
            source="测试材料",
            observed_at=now(),
            verified_at=now(),
            verified_by=users["supervisor"],
            uploaded_by=users["staff"],
            content_hash="b" * 64,
        )
        db.add(evidence)
        db.flush()
        consent = Evidence(
            tenant_id=tenant.id,
            ticket_id=ticket.id,
            kind="customer_request",
            title="客户退款意愿",
            body="合成测试客户明确选择原路退款",
            source="测试材料",
            observed_at=now(),
            verified_at=now(),
            verified_by=users["supervisor"],
            uploaded_by=users["staff"],
            content_hash="c" * 64,
        )
        db.add(consent)
        db.flush()
        ids = {
            "tenant": tenant.id,
            "other": other.id,
            "order": order.id,
            "policy": policy.id,
            "ticket": ticket.id,
            "evidence": evidence.id,
            "consent": consent.id,
            "users": users,
        }
    clients = []

    def login(role):
        client = TestClient(app)
        clients.append(client)
        result = client.post(
            "/api/v1/auth/login",
            json={"email": role + "@test.local", "password": PASSWORD},
            headers={"origin": ORIGIN},
        )
        assert result.status_code == 200, result.text
        client.headers.update({"origin": ORIGIN, "x-csrf-token": result.json()["csrf"]})
        return client

    yield ids, login
    for client in clients:
        client.close()


@pytest.fixture
def approved(world):
    ids, login = world
    staff = login("staff")
    supervisor = login("supervisor")
    executor = login("executor")
    result = staff.post(
        f"/api/v1/tickets/{ids['ticket']}/proposals",
        json={
            "amount_cents": 12900,
            "reason": "测试丢件已经核验，客户选择原路退款",
            "policy_id": ids["policy"],
            "evidence_ids": [ids["evidence"], ids["consent"]],
        },
    )
    assert result.status_code == 201, result.text
    proposal = result.json()
    result = supervisor.post(
        "/api/v1/approvals/" + proposal["id"] + "/decisions",
        json={
            "decision": "approve",
            "payload_hash": proposal["payload_hash"],
            "note": "",
        },
    )
    assert result.status_code == 200, result.text
    return ids, staff, supervisor, executor, proposal, result.json()["action"]
