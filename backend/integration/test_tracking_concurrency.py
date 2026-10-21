"""Use real PostgreSQL locks; only the external logistics response is simulated."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app import logistics
from app.config import settings
from app.database import SessionLocal, engine, set_tenant
from app.models import Membership, Order
from sqlalchemy import select
from test_postgres import pgworld as pgworld

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql", reason="PostgreSQL integration only"
)


def test_ten_concurrent_queries_only_call_provider_once(pgworld, monkeypatch):
    ids, _ = pgworld
    cfg = settings()
    monkeypatch.setattr(cfg, "kuaidi100_customer", "synthetic-customer")
    monkeypatch.setattr(cfg, "kuaidi100_key", "synthetic-key")
    with SessionLocal.begin() as db:
        set_tenant(db, ids["tenant"])
        staff_id = db.scalar(
            select(Membership.user_id).where(
                Membership.tenant_id == ids["tenant"], Membership.role == "staff"
            )
        )
        order = db.get(Order, ids["order"])
        order.carrier_code, order.tracking_number = "yuantong", "SIM12345678"
    calls = []

    def reply(*args):
        calls.append(args)
        time.sleep(0.15)
        return {"source": "快递100格式模拟", "status": "ok", "state": "0", "events": []}

    monkeypatch.setattr(logistics, "request_tracking", reply)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(
                lambda _: logistics.query_logistics(
                    ids["tenant"], ids["tickets"][0][0], staff_id
                ),
                range(10),
            )
        )
    assert len(calls) == 1
    assert all(x["status"] in {"ok", "querying"} for x in results)
    assert sum(x.get("cached", False) for x in results) == 9
