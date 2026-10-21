import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from app import logistics
from app.config import Settings, settings
from app.database import SessionLocal
from app.importing import parse_csv
from app.models import AgentRun, Job, Order, Proposal, Tenant, now
from app.worker import run_once
from sqlalchemy import func, select
from test_agent_runtime import provider, start


@pytest.mark.parametrize("logistics_configured", [False, True])
def test_context_distinguishes_commerce_from_logistics(
    world, configured, monkeypatch, logistics_configured
):
    from app import agent

    ids, login = world
    monkeypatch.setattr(
        settings(), "kuaidi100_customer", "synthetic-customer" if logistics_configured else ""
    )
    monkeypatch.setattr(
        settings(), "kuaidi100_key", "synthetic-key" if logistics_configured else ""
    )
    run_id = start(login("staff"), ids)
    with SessionLocal() as db:
        context = agent.make_context(
            db, db.get(AgentRun, run_id), {
                "observations": [
                    {"tool": "query_logistics", "result": {"status": "no_result"}}
                ]
            }
        )
    assert context["constraints"]["commerce_platform_connected"] is False
    assert context["constraints"]["logistics_configured"] is logistics_configured
    assert "external_platform_connected" not in context["constraints"]
    assert "synthetic-key" not in json.dumps(context)


@pytest.fixture
def shipping(world, monkeypatch):
    ids, login = world
    cfg = settings()
    monkeypatch.setattr(cfg, "kuaidi100_customer", "synthetic-customer")
    monkeypatch.setattr(cfg, "kuaidi100_key", "synthetic-provider-key")
    with SessionLocal.begin() as db:
        order = db.get(Order, ids["order"])
        order.carrier_code = "yuantong"
        order.tracking_number = "SIM12345678"
        order.tracking_phone = "13912345678"
    return ids, login


def test_secret_file_optional_and_hidden(tmp_path):
    file = tmp_path / "key"
    file.write_text("synthetic-secret")
    cfg = Settings(_env_file=None, kuaidi100_key_file=file)
    assert cfg.kuaidi100_key == "synthetic-secret"
    assert "synthetic-secret" not in repr(cfg) + cfg.model_dump_json()
    assert (
        Settings(
            _env_file=None, llm_api_key_file="", kuaidi100_key_file=""
        ).kuaidi100_key_file
        is None
    )


def test_csv_accepts_waybill_and_public_order_hides_verification_phone(world):
    ids, login = world
    csv = (
        "订单号,商品名称,数量,实付金额,订单状态,下单时间,"
        "快递公司编码,运单号,查件校验号码\n"
        "SIM-1,合成商品,1,10.00,已发货,2026-10-19T00:00:00Z,"
        "yuantong,SIM12345678,13912345678\n"
    )
    rows, errors = parse_csv(csv.encode())
    assert not errors and rows[0]["tracking_number"] == "SIM12345678"
    preview = login("staff").post(
        "/api/v1/imports/preview",
        files={"file": ("synthetic.csv", csv.encode(), "text/csv")},
    )
    assert preview.status_code == 200
    assert "13912345678" not in preview.text
    with SessionLocal.begin() as db:
        db.get(Order, ids["order"]).tracking_phone = "13912345678"
    result = login("staff").get("/api/v1/tickets/" + ids["ticket"]).json()
    assert "tracking_phone" not in result["order"]
    assert result["order"]["tracking_phone_configured"]
    assert "13912345678" not in json.dumps(result)


def test_query_missing_configuration_is_explicit_and_tenant_scoped(world, monkeypatch):
    ids, login = world
    with SessionLocal.begin() as db:
        order = db.get(Order, ids["order"])
        order.carrier_code, order.tracking_number = "yuantong", "SIM12345678"
    monkeypatch.setattr(
        logistics, "request_tracking", lambda *a: pytest.fail("must not call provider")
    )
    client = login("staff")
    assert (
        client.post("/api/v1/tickets/" + ids["ticket"] + "/logistics").json()["status"]
        == "unconfigured"
    )
    assert (
        login("outside")
        .post("/api/v1/tickets/" + ids["ticket"] + "/logistics")
        .status_code
        == 404
    )
    assert (
        login("executor")
        .post("/api/v1/tickets/" + ids["ticket"] + "/logistics")
        .status_code
        == 403
    )


def test_provider_signs_exact_payload_and_redacts_response(shipping, monkeypatch):
    calls = []
    original = httpx.Client

    def reply(request):
        payload = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        calls.append(payload)
        assert str(request.url) == "https://poll.kuaidi100.com/poll/query.do"
        expected = (
            hashlib.md5(
                (
                    payload["param"] + "synthetic-provider-key" + "synthetic-customer"
                ).encode()
            )
            .hexdigest()
            .upper()
        )
        assert payload["sign"] == expected
        return httpx.Response(
            200,
            json={
                "com": "yuantong",
                "nu": "SIM12345678",
                "status": "200",
                "state": "2",
                "data": [
                    {
                        "time": "2026-10-19 10:00:00",
                        "context": "无法联系收件人13912345678",
                        "statusCode": "206",
                    }
                ],
            },
        )

    monkeypatch.setattr(
        logistics.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(reply), **kw),
    )
    result = logistics.request_tracking("yuantong", "SIM12345678", "13912345678")
    assert result["status"] == "ok" and result["state_label"] == "无法联系"
    assert not result["is_loss_proof"] and not result["reason_confirmed"]
    assert "13912345678" not in json.dumps(result)
    assert "synthetic-provider-key" not in json.dumps(calls)


@pytest.mark.parametrize(
    "kind", ["timeout", "mismatch", "no_result", "malformed", "oversized"]
)
def test_provider_errors_are_safe(shipping, monkeypatch, kind):
    original = httpx.Client

    def reply(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("synthetic-provider-key must never escape")
        if kind == "oversized":
            return httpx.Response(200, content=b"x" * (256 * 1024 + 1))
        raw = (
            {"com": "other", "nu": "wrong", "status": "200", "data": []}
            if kind == "mismatch"
            else {"returnCode": "500", "message": "synthetic-provider-key"}
        )
        return httpx.Response(
            200,
            content=b"not json" if kind == "malformed" else json.dumps(raw).encode(),
        )

    monkeypatch.setattr(
        logistics.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(reply), **kw),
    )
    result = logistics.request_tracking("yuantong", "SIM12345678", "")
    assert result["status"] in {"failed", "no_result"}
    assert "synthetic-provider-key" not in json.dumps(result)


def test_query_reuses_cache_including_failures(shipping, monkeypatch):
    ids, login = shipping
    calls = []
    monkeypatch.setattr(
        logistics,
        "request_tracking",
        lambda *a: (
            calls.append(a)
            or {"source": "快递100格式模拟", "status": "failed", "message": "合成超时"}
        ),
    )
    client = login("staff")
    path = "/api/v1/tickets/" + ids["ticket"] + "/logistics"
    assert client.post(path).json()["status"] == "failed"
    assert client.post(path).json()["cached"] is True
    assert len(calls) == 1


def test_new_customer_ticket_automatically_queues_one_run(world, configured):
    ids, login = world
    client = login("staff")
    result = client.post(
        "/api/v1/tickets",
        json={
            "order_id": ids["order"],
            "title": "客户问包裹位置",
            "request": "客户询问为什么一直没收到",
        },
    )
    assert result.status_code == 201, result.text
    assert result.json()["state"] == "investigating"
    run_id = result.json()["investigation"]["run_id"]
    again = client.post("/api/v1/tickets/" + result.json()["id"] + "/runs")
    assert again.json()["id"] == run_id
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Job.id))) == 1


def test_duplicate_table_intake_only_starts_one_investigation(
    world, configured, monkeypatch
):
    ids, login = world
    monkeypatch.setattr(settings(), "demo_mode", True)
    with SessionLocal.begin() as db:
        db.get(Tenant, ids["tenant"]).is_demo = True
    client = login("staff")
    payload = {
        "base_id": "demo",
        "table_id": "demo",
        "record_id": "recAuto",
        "channel": "商家导入",
        "external_id": "ORDER-1",
        "title": "合成物流询问",
        "request": "合成客户希望查清包裹位置",
        "issue": "物流长期未更新",
    }
    first = client.post("/api/v1/integrations/feishu/simulate", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["investigation"]["state"] == "queued"
    again = client.post("/api/v1/integrations/feishu/simulate", json=payload)
    assert not again.json()["created"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AgentRun.id))) == 1
        assert db.scalar(select(func.count(Job.id))) == 1


def test_query_cache_expires_and_order_edits_keep_private_phone(shipping, monkeypatch):
    ids, login = shipping
    calls = []
    monkeypatch.setattr(
        logistics,
        "request_tracking",
        lambda *a: (
            calls.append(a)
            or {"source": "快递100格式模拟", "status": "ok", "state": "0"}
        ),
    )
    client = login("staff")
    path = "/api/v1/tickets/" + ids["ticket"] + "/logistics"
    client.post(path)
    with SessionLocal.begin() as db:
        db.get(Order, ids["order"]).logistics_queried_at = now() - timedelta(minutes=31)
    client.post(path)
    assert len(calls) == 2
    order = client.get("/api/v1/tickets/" + ids["ticket"]).json()["order"]
    from app.schemas import OrderInput

    payload = {k: v for k, v in order.items() if k in OrderInput.model_fields}
    result = client.put(
        "/api/v1/orders/" + ids["order"],
        json=payload | {"version": order["version"], "tracking_number": "SIMNEW123456"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["logistics_snapshot"] == {}
    assert result.json()["tracking_phone_configured"]
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).tracking_phone == "13912345678"


@pytest.mark.parametrize("signed", [{"state": "3"}, {"state": "0", "delivered": True}])
def test_agent_can_query_logistics_but_cannot_refund_delivered_order(
    shipping, configured, monkeypatch, signed
):
    ids, login = shipping
    monkeypatch.setattr(
        logistics,
        "request_tracking",
        lambda *a: {
            "source": "快递100格式模拟（合成）",
            "simulated": True,
            "status": "ok",
            **signed,
            "events": [],
        },
    )
    requests = provider(
        monkeypatch,
        [
            {"tool": "query_logistics", "rationale": "先查当前运单的物流"},
            {
                "tool": "draft_refund",
                "rationale": "测试错误地尝试按丢件退款",
                "amount_cents": 12900,
                "policy_id": ids["policy"],
                "evidence_ids": [ids["evidence"], ids["consent"]],
            },
            {"tool": "finish", "rationale": "签收与客户诉求冲突，需要人工核查"},
        ],
    )
    run_id = start(login("staff"), ids)
    run_once()
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).state == "human_needed"
        assert db.scalar(select(Proposal)) is None
    assert "13912345678" not in json.dumps(requests)
    assert "logistics_conflict" in json.dumps(requests)
