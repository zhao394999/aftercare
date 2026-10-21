"""Requires migrated PostgreSQL and a non-owner aftercare_app role; run by CI."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from app.database import SessionLocal, engine, set_tenant
from app.main import app
from app.models import (
    Action,
    Evidence,
    Job,
    Membership,
    Order,
    Policy,
    Tenant,
    Ticket,
    User,
    now,
    uid,
)
from app.security import hasher
from app.worker import claim_job

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql", reason="PostgreSQL integration only"
)


@pytest.fixture
def pgworld():
    suffix = uid()
    password = "Integration-fixture-password-12"
    pw = hasher.hash(password)
    with SessionLocal.begin() as db:
        tenant = Tenant(name="PG商家 " + suffix, config={})
        other = Tenant(name="隔离商家 " + suffix, config={})
        db.add_all([tenant, other])
        db.flush()
        users = {}
        for role in ["staff", "supervisor"]:
            user = User(
                name=role, email=role + suffix + "@test.local", password_hash=pw
            )
            db.add(user)
            db.flush()
            db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
            users[role] = (user.id, user.email)
        set_tenant(db, tenant.id)
        order = Order(
            tenant_id=tenant.id,
            external_id=suffix,
            product="并发订单",
            paid_cents=12900,
            placed_at=now() - timedelta(days=5),
            verified_at=now(),
            status="shipped",
        )
        policy = Policy(
            tenant_id=tenant.id,
            title="丢件政策",
            body="丢件确认且客户选择退款后可提案",
            state="published",
            effective_from=now() - timedelta(days=100),
            allow_refund_on_loss=True,
            max_refund_cents=100000,
            content_hash="a" * 64,
        )
        db.add_all([order, policy])
        db.flush()
        tickets = []
        for n in range(10):
            ticket = Ticket(
                tenant_id=tenant.id,
                number=suffix + str(n),
                order_id=order.id,
                title="测试丢件",
                request="客户要求退款",
                owner_id=users["staff"][0],
                due_at=now() + timedelta(hours=24),
                evidence_version=2,
            )
            db.add(ticket)
            db.flush()
            evidence_ids = []
            for kind in ["carrier_confirmation", "customer_request"]:
                ev = Evidence(
                    tenant_id=tenant.id,
                    ticket_id=ticket.id,
                    kind=kind,
                    title=kind,
                    body="合成测试已核验材料",
                    source="测试",
                    observed_at=now(),
                    verified_at=now(),
                    verified_by=users["supervisor"][0],
                    uploaded_by=users["staff"][0],
                    content_hash="b" * 64,
                )
                db.add(ev)
                db.flush()
                evidence_ids.append(ev.id)
            tickets.append((ticket.id, evidence_ids))
        ids = {
            "tenant": tenant.id,
            "other": other.id,
            "order": order.id,
            "policy": policy.id,
            "tickets": tickets,
        }

    def login(role):
        client = TestClient(app)
        result = client.post(
            "/api/v1/auth/login",
            json={"email": users[role][1], "password": password},
            headers={"origin": "http://127.0.0.1:5173"},
        )
        assert result.status_code == 200, result.text
        client.headers.update(
            {"origin": "http://127.0.0.1:5173", "x-csrf-token": result.json()["csrf"]}
        )
        return client

    return ids, login


def test_rls_without_scope_and_wrong_scope_is_fail_closed(pgworld):
    ids, _ = pgworld
    with SessionLocal.begin() as db:
        assert db.scalar(select(Order).where(Order.id == ids["order"])) is None
        set_tenant(db, ids["other"])
        assert db.scalar(select(Order).where(Order.id == ids["order"])) is None
        set_tenant(db, ids["tenant"])
        assert db.scalar(select(Order).where(Order.id == ids["order"])) is not None
    with SessionLocal.begin() as db:
        assert db.scalar(select(Order).where(Order.id == ids["order"])) is None
    with pytest.raises(DBAPIError):
        with SessionLocal.begin() as db:
            set_tenant(db, ids["other"])
            db.add(
                Order(
                    tenant_id=ids["tenant"],
                    external_id=uid(),
                    product="非法越界",
                    paid_cents=100,
                    placed_at=now(),
                )
            )
            db.flush()


def test_concurrent_approvals_only_reserve_once_per_order(pgworld):
    ids, login = pgworld
    staff = login("staff")
    proposals = []
    for ticket, evidence_ids in ids["tickets"]:
        result = staff.post(
            "/api/v1/tickets/" + ticket + "/proposals",
            json={
                "amount_cents": 12900,
                "reason": "丢件已确认且客户选择退款",
                "policy_id": ids["policy"],
                "evidence_ids": evidence_ids,
            },
        )
        assert result.status_code == 201, result.text
        proposals.append(result.json())
    barrier = Barrier(10)

    def approve(proposal):
        with login("supervisor") as client:
            barrier.wait(timeout=10)
            return client.post(
                "/api/v1/approvals/" + proposal["id"] + "/decisions",
                json={"decision": "approve", "payload_hash": proposal["payload_hash"]},
            ).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(approve, proposals))
    assert sorted(results) == [200] + [409] * 9
    with SessionLocal.begin() as db:
        set_tenant(db, ids["tenant"])
        assert db.get(Order, ids["order"]).reserved_cents == 12900
        assert (
            len(db.scalars(select(Action).where(Action.order_id == ids["order"])).all())
            == 1
        )
    staff.close()


def test_duplicate_dispatch_has_one_database_lease(pgworld):
    ids, _ = pgworld
    with SessionLocal.begin() as db:
        job = Job(tenant_id=ids["tenant"], kind="import", resource_id=uid())
        db.add(job)
        db.flush()
        job_id = job.id
    barrier = Barrier(2)

    def claim(_):
        barrier.wait(timeout=10)
        return claim_job(job_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(r is not None for r in results) == 1


def test_audit_records_cannot_be_mutated_by_runtime_role(pgworld):
    ids, login = pgworld
    client = login("staff")
    with pytest.raises(DBAPIError):
        with SessionLocal.begin() as db:
            set_tenant(db, ids["tenant"])
            db.execute(
                text("DELETE FROM audit_events WHERE tenant_id=:id"),
                {"id": ids["tenant"]},
            )
    client.close()


def test_concurrent_intake_retries_return_one_order_and_one_ticket(pgworld):
    ids, login = pgworld
    request_id = uid()
    body = {
        "request_id": request_id,
        "order": {
            "external_id": request_id,
            "product": "并发录入合成商品",
            "quantity": 3,
            "paid_cents": 3900,
            "placed_at": now().isoformat() + "Z",
        },
        "ticket": {
            "title": "并发录入物流异常",
            "request": "合成客户要求查询物流",
            "issue": "logistics_delay",
            "auto_investigate": False,
        },
    }
    barrier = Barrier(4)

    def submit(_):
        with login("staff") as client:
            barrier.wait(timeout=10)
            result = client.post("/api/v1/orders/intake", json=body)
            assert result.status_code == 201, result.text
            return result.json()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert len({result["order"]["id"] for result in results}) == 1
    assert len({result["ticket"]["id"] for result in results}) == 1
    with SessionLocal.begin() as db:
        set_tenant(db, ids["tenant"])
        orders = db.scalars(select(Order).where(Order.external_id == request_id)).all()
        assert len(orders) == 1
        assert (
            len(db.scalars(select(Ticket).where(Ticket.order_id == orders[0].id)).all())
            == 1
        )
