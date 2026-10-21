from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from app import domain as d
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Membership, Order, Ticket, now
from fastapi.testclient import TestClient
from sqlalchemy import func, select


@pytest.fixture
def feishu(world, monkeypatch):
    ids, login = world
    cfg = settings()
    for name, value in {
        "feishu_intake_token": "synthetic-test-token-" + "x" * 32,
        "feishu_tenant_id": ids["tenant"],
        "feishu_owner_id": ids["users"]["staff"],
        "feishu_base_id": "bascnSynthetic",
        "feishu_table_id": "tblSynthetic",
    }.items():
        monkeypatch.setattr(cfg, name, value)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + cfg.feishu_intake_token
    payload = {
        "base_id": cfg.feishu_base_id,
        "table_id": cfg.feishu_table_id,
        "record_id": "recSynthetic1",
        "channel": "商家导入",
        "external_id": "ORDER-1",
        "title": "【合成飞书记录】物流停滞",
        "request": "合成客户希望核查物流进展",
        "issue": "物流长期未更新",
    }
    yield ids, login, client, payload
    client.close()


def test_feishu_record_creates_one_ticket_and_returns_live_status(feishu):
    ids, login, client, payload = feishu
    first = client.post("/api/v1/integrations/feishu/records", json=payload)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["created"] and result["state"] == "new"
    assert result["state_label"] == "待调查"
    assert result["url"].endswith("/tickets/" + result["ticket_id"])
    again = client.post("/api/v1/integrations/feishu/records", json=payload)
    assert again.status_code == 200 and not again.json()["created"]
    assert again.json()["ticket_id"] == result["ticket_id"]
    with SessionLocal.begin() as db:
        ticket = db.get(Ticket, result["ticket_id"])
        assert ticket.owner_id == ids["users"]["staff"]
        assert ticket.issue == "logistics_delay" and ticket.evidence_version == 0
        assert ticket.source_ref == "feishu:bascnSynthetic:tblSynthetic:recSynthetic1"
        ticket.state = "waiting_evidence"
        assert (
            db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.event == "ticket.feishu_created"
                )
            )
            == 1
        )
    status = client.get("/api/v1/integrations/feishu/records/recSynthetic1")
    assert status.status_code == 200
    assert status.json()["state_label"] == "待补证"
    changed = client.post(
        "/api/v1/integrations/feishu/records",
        json=payload | {"request": "修改后的诉求"},
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "source_record_changed"
    detail = login("staff").get("/api/v1/tickets/" + result["ticket_id"]).json()
    assert detail["request"] == payload["request"]


@pytest.mark.parametrize(
    "problem", ["token", "table", "order", "channel", "issue", "revoked"]
)
def test_feishu_rejects_invalid_or_untrusted_input(feishu, problem):
    ids, _, client, payload = feishu
    if problem == "token":
        client.headers["Authorization"] = "Bearer invalid-token"
    elif problem == "table":
        payload["table_id"] = "tblOther"
    elif problem == "order":
        with SessionLocal.begin() as db:
            db.add(
                Order(
                    tenant_id=ids["other"],
                    external_id="OTHER-ORDER",
                    product="其他租户订单",
                    paid_cents=1000,
                    placed_at=now(),
                )
            )
        payload["external_id"] = "OTHER-ORDER"
    elif problem == "channel":
        payload["channel"] = "其他店铺"
    elif problem == "issue":
        payload["issue"] = "商品破损换货"
    elif problem == "revoked":
        with SessionLocal.begin() as db:
            member = db.scalar(
                select(Membership).where(Membership.user_id == ids["users"]["staff"])
            )
            member.enabled = False
    response = client.post("/api/v1/integrations/feishu/records", json=payload)
    assert response.status_code in {401, 403, 404, 422}, response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Ticket.id))) == 1


def test_feishu_concurrent_delivery_is_idempotent(feishu):
    _, _, client, payload = feishu

    def deliver(_):
        with TestClient(app) as sender:
            return sender.post(
                "/api/v1/integrations/feishu/records",
                json=payload,
                headers={"Authorization": client.headers["Authorization"]},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(deliver, range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert len({r.json()["ticket_id"] for r in responses}) == 1
    assert sum(r.json()["created"] for r in responses) == 1


def test_feishu_simulation_requires_demo_and_ticket_permission(world, monkeypatch):
    ids, login = world
    payload = {
        "base_id": "demo",
        "table_id": "demo",
        "record_id": "recDemo",
        "channel": "商家导入",
        "external_id": "ORDER-1",
        "title": "合成模拟工单",
        "request": "合成客户询问物流进展",
        "issue": "物流长期未更新",
    }
    staff = login("staff")
    assert (
        staff.post("/api/v1/integrations/feishu/simulate", json=payload).status_code
        == 404
    )
    monkeypatch.setattr(settings(), "demo_mode", True)
    with SessionLocal.begin() as db:
        from app.models import Tenant

        db.get(Tenant, ids["tenant"]).is_demo = True
    assert (
        login("executor")
        .post("/api/v1/integrations/feishu/simulate", json=payload)
        .status_code
        == 403
    )
    assert (
        staff.post("/api/v1/integrations/feishu/simulate", json=payload).status_code
        == 200
    )


def test_homepage_filters_match_counts_and_include_all_pages(world, monkeypatch):
    ids, login = world
    monkeypatch.setattr(d, "now", lambda: datetime(2026, 10, 18, 8))
    start = datetime(2026, 10, 17, 16)
    with SessionLocal.begin() as db:
        for index in range(30):
            db.add(
                Ticket(
                    tenant_id=ids["tenant"],
                    order_id=ids["order"],
                    number=f"OPEN-{index}",
                    title="待处理合成工单",
                    request="模拟客户的物流咨询",
                    owner_id=ids["users"]["staff"],
                    state="waiting_approval" if index < 5 else "waiting_evidence",
                    due_at=now() + timedelta(days=1),
                    created_at=start,
                )
            )
        for index, closed_at in enumerate(
            [
                start - timedelta(microseconds=1),
                start,
                start + timedelta(days=1) - timedelta(microseconds=1),
                start + timedelta(days=1),
            ]
        ):
            db.add(
                Ticket(
                    tenant_id=ids["tenant"],
                    order_id=ids["order"],
                    number=f"CLOSED-{index}",
                    title="已闭环合成工单",
                    request="模拟已闭环客户咨询",
                    owner_id=ids["users"]["staff"],
                    state="closed",
                    closed_at=closed_at,
                    due_at=now(),
                )
            )
        other_order = Order(
            tenant_id=ids["other"],
            external_id="OTHER",
            product="其他商家商品",
            paid_cents=1000,
            placed_at=now(),
        )
        db.add(other_order)
        db.flush()
        db.add(
            Ticket(
                tenant_id=ids["other"],
                order_id=other_order.id,
                number="OTHER-TICKET",
                title="其他租户工单",
                request="不应显示",
                owner_id="outsider",
                due_at=now(),
            )
        )
    client = login("staff")
    overview = client.get("/api/v1/overview").json()
    for state, count in {
        "open": 31,
        "waiting_approval": 5,
        "waiting_evidence": 25,
        "closed_today": 2,
    }.items():
        first = client.get("/api/v1/tickets", params={"state": state}).json()
        if state == "open":
            open_ids = {row["id"] for row in first["items"]}
        assert first["total"] == count
        expected = (
            overview["states"][state]
            if state in {"waiting_approval", "waiting_evidence"}
            else overview[state]
        )
        assert first["total"] == expected
        assert all(row["tenant_id"] == ids["tenant"] for row in first["items"])
    second = client.get("/api/v1/tickets?state=open&page=2").json()
    assert second["total"] == 31 and len(second["items"]) == 6
    assert len(open_ids | {row["id"] for row in second["items"]}) == 31
    filtered = client.get("/api/v1/tickets?state=closed_today&q=CLOSED-1").json()
    assert filtered["total"] == 1
