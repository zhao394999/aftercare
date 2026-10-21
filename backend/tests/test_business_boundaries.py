from datetime import timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Action, Membership, Order, Policy, Ticket, now
from app.worker import run_once


def claim(executor, ids, action):
    result = executor.post(
        "/api/v1/actions/" + action["id"] + "/claim",
        json={
            "order_version": 1,
            "checked_at": now().isoformat() + "Z",
            "current_state_confirmed": True,
        },
    )
    assert result.status_code == 200, result.text


def evidence(client, ids, kind="execution_receipt"):
    result = client.post(
        f"/api/v1/tickets/{ids['ticket']}/evidence",
        data={
            "kind": kind,
            "title": "测试外部业务凭证",
            "source": "测试系统核查",
            "body": "已对照外部系统核查实际记录与状态",
            "observed_at": now().isoformat() + "Z",
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_tenant_boundary(world):
    ids, login = world
    other = login("outside")
    assert other.get("/api/v1/tickets/" + ids["ticket"]).status_code == 404
    assert (
        other.post(
            "/api/v1/orders/" + ids["order"] + "/verify", json={"version": 1}
        ).status_code
        == 404
    )
    assert other.get("/api/v1/orders").json()["total"] == 0
    assert other.get("/api/v1/evidence/" + ids["evidence"] + "/file").status_code == 404


def test_csrf_and_role_guards(world):
    ids, login = world
    staff = login("staff")
    assert (
        staff.post("/api/v1/policies/" + ids["policy"] + "/retire").status_code == 403
    )
    staff.headers.pop("x-csrf-token")
    assert (
        staff.post(
            "/api/v1/orders/" + ids["order"] + "/verify", json={"version": 1}
        ).status_code
        == 403
    )


def test_self_approval_is_blocked(world):
    ids, login = world
    admin = login("admin")
    p = admin.post(
        "/api/v1/tickets/" + ids["ticket"] + "/proposals",
        json={
            "amount_cents": 12900,
            "reason": "丢件事实经人工核查，客户选择退款",
            "policy_id": ids["policy"],
            "evidence_ids": [ids["evidence"], ids["consent"]],
        },
    ).json()
    result = admin.post(
        "/api/v1/approvals/" + p["id"] + "/decisions",
        json={"decision": "approve", "payload_hash": p["payload_hash"]},
    )
    assert result.status_code == 403 and result.json()["code"] == "self_approval"
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 0


def test_approval_retry_does_not_reserve_twice(approved):
    ids, staff, supervisor, executor, proposal, action = approved
    result = supervisor.post(
        "/api/v1/approvals/" + proposal["id"] + "/decisions",
        json={"decision": "approve", "payload_hash": proposal["payload_hash"]},
    )
    assert result.status_code == 200 and result.json()["action"]["id"] == action["id"]
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 12900
        assert len(db.scalars(select(Action)).all()) == 1


def test_changed_evidence_blocks_execution(approved):
    ids, staff, supervisor, executor, p, action = approved
    evidence(staff, ids, "logistics")
    result = executor.post(
        "/api/v1/actions/" + action["id"] + "/claim",
        json={
            "order_version": 1,
            "checked_at": now().isoformat() + "Z",
            "current_state_confirmed": True,
        },
    )
    assert result.status_code == 409 and result.json()["code"] == "evidence_changed"
    result = supervisor.post(
        "/api/v1/actions/" + action["id"] + "/cancel",
        json={"note": "关键证据变化，撤销尚未领取任务"},
    )
    assert result.status_code == 200
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 0


def test_claimed_action_cannot_be_cancelled(approved):
    ids, staff, supervisor, executor, p, action = approved
    claim(executor, ids, action)
    result = supervisor.post(
        "/api/v1/actions/" + action["id"] + "/cancel",
        json={"note": "尝试取消已经领取的外部动作"},
    )
    assert result.status_code == 409
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 12900


def test_processing_is_not_success_and_final_verification_closes_ledger(approved):
    ids, staff, supervisor, executor, p, action = approved
    claim(executor, ids, action)
    receipt_id = evidence(executor, ids)
    body = {
        "external_ref": "TEST-REFUND-1",
        "external_state": "processing",
        "actual_cents": 12900,
        "evidence_id": receipt_id,
    }
    result = executor.post("/api/v1/actions/" + action["id"] + "/receipt", json=body)
    assert result.json()["state"] == "processing"
    result = supervisor.post(
        "/api/v1/actions/" + action["id"] + "/verify",
        json={"confirmed": True, "note": "外部当前仍然处于受理处理中"},
    )
    assert result.status_code == 409
    body["external_state"] = "succeeded"
    assert (
        executor.post(
            "/api/v1/actions/" + action["id"] + "/receipt", json=body
        ).status_code
        == 200
    )
    result = supervisor.post(
        "/api/v1/actions/" + action["id"] + "/verify",
        json={"confirmed": True, "note": "已对照外部流水核对终态与金额"},
    )
    assert result.status_code == 409 and result.json()["code"] == "receipt_not_verified"
    assert (
        supervisor.post("/api/v1/evidence/" + receipt_id + "/verify").status_code == 200
    )
    result = supervisor.post(
        "/api/v1/actions/" + action["id"] + "/verify",
        json={"confirmed": True, "note": "已核验外部终态成功，金额与流水一致"},
    )
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 0
        assert db.get(Order, ids["order"]).refunded_cents == 12900
        assert db.get(Ticket, ids["ticket"]).state == "resolved"
    assert staff.post("/api/v1/tickets/" + ids["ticket"] + "/close").status_code == 409
    assert (
        staff.post(
            "/api/v1/tickets/" + ids["ticket"] + "/communications",
            json={
                "channel": "商家客服渠道",
                "content": "已向客户告知退款完成并确认处理结果",
            },
        ).status_code
        == 200
    )
    assert (
        staff.post("/api/v1/tickets/" + ids["ticket"] + "/close").json()["state"]
        == "closed"
    )


def test_failed_verification_keeps_reservation_and_requires_reconciliation(approved):
    ids, staff, supervisor, executor, p, action = approved
    claim(executor, ids, action)
    receipt_id = evidence(executor, ids)
    executor.post(
        "/api/v1/actions/" + action["id"] + "/receipt",
        json={
            "external_ref": "REF-2",
            "external_state": "succeeded",
            "actual_cents": 12900,
            "evidence_id": receipt_id,
        },
    )
    result = supervisor.post(
        "/api/v1/actions/" + action["id"] + "/verify",
        json={"confirmed": False, "note": "外部结果与提供凭证不符，需要先对账"},
    )
    assert result.json()["state"] == "reconciling"
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 12900
        assert db.get(Ticket, ids["ticket"]).state == "reconciling"
    result = supervisor.post(
        "/api/v1/actions/" + action["id"] + "/reconcile",
        json={
            "confirmed_not_executed": False,
            "evidence_id": ids["evidence"],
            "note": "结果尚未查明，不能重新执行该动作",
        },
    )
    assert result.status_code == 409


def test_model_is_never_faked_when_unconfigured(world):
    ids, login = world
    staff = login("staff")
    result = staff.post("/api/v1/tickets/" + ids["ticket"] + "/runs")
    assert result.status_code == 503 and result.json()["code"] == "model_unconfigured"
    with SessionLocal() as db:
        assert db.get(Ticket, ids["ticket"]).state == "new"


def test_csv_import_is_durable_and_existing_orders_are_skipped(world):
    ids, login = world
    staff = login("staff")
    content = "订单号,商品名称,数量,实付金额,订单状态,下单时间\nNEW-2,测试杯,1,29.90,已发货,2026-10-12T08:00:00Z\nORDER-1,已有商品,1,129.00,已发货,2026-10-12T08:00:00Z\nBAD,缺金额,1,abc,已发货,2026-10-12T08:00:00Z\n"
    result = staff.post(
        "/api/v1/imports/preview",
        files={"file": ("orders.csv", content.encode(), "text/csv")},
    )
    assert result.status_code == 200, result.text
    preview = result.json()
    assert preview["valid_count"] == 2 and preview["error_count"] == 1
    assert (
        staff.post("/api/v1/imports/" + preview["id"] + "/confirm").status_code == 202
    )
    assert run_once()
    job = staff.get("/api/v1/imports").json()[0]
    assert job["state"] == "completed" and job["inserted"] == 1 and job["skipped"] == 1
    assert staff.get("/api/v1/orders").json()["total"] == 2


def test_partial_success_settles_only_proved_amount_and_allows_new_proposal(approved):
    ids, staff, supervisor, executor, p, action = approved
    claim(executor, ids, action)
    receipt_id = evidence(executor, ids)
    result = executor.post(
        "/api/v1/actions/" + action["id"] + "/receipt",
        json={
            "external_ref": "PARTIAL-REF-1",
            "external_state": "succeeded",
            "actual_cents": 10000,
            "evidence_id": receipt_id,
        },
    )
    assert result.json()["state"] == "reconciling"
    proof = evidence(supervisor, ids, "reconciliation")
    for item in [proof, receipt_id]:
        assert (
            supervisor.post("/api/v1/evidence/" + item + "/verify").status_code == 200
        )
    body = {
        "actual_cents": 10000,
        "evidence_id": proof,
        "note": "已独立核对外部实际退款100元，剩余29元需要重新处理",
    }
    assert (
        supervisor.post(
            "/api/v1/actions/" + action["id"] + "/settle", json=body
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        order = db.get(Order, ids["order"])
        assert order.refunded_cents == 10000 and order.reserved_cents == 0
        assert db.get(Ticket, ids["ticket"]).state == "draft"
        version = order.version
    assert (
        staff.post(
            "/api/v1/orders/" + ids["order"] + "/verify", json={"version": version}
        ).status_code
        == 200
    )
    result = staff.post(
        "/api/v1/tickets/" + ids["ticket"] + "/proposals",
        json={
            "amount_cents": 2900,
            "reason": "客户原先选择全额退款，现对剩余部分重新提案",
            "policy_id": ids["policy"],
            "evidence_ids": [ids["evidence"], ids["consent"]],
        },
    )
    assert result.status_code == 201, result.text


def test_historical_refunds_reduce_available_amount(world):
    ids, login = world
    staff = login("staff")
    supervisor = login("supervisor")
    order = staff.get("/api/v1/orders").json()["items"][0]
    payload = {
        k: order[k]
        for k in [
            "external_id",
            "channel",
            "product",
            "quantity",
            "paid_cents",
            "customer",
            "status",
            "placed_at",
            "shipped_at",
            "promised_at",
            "version",
        ]
    }
    payload["refunded_cents"] = 5000
    assert staff.put("/api/v1/orders/" + ids["order"], json=payload).status_code == 403
    response = supervisor.put("/api/v1/orders/" + ids["order"], json=payload)
    assert response.status_code == 200, response.text
    assert (
        supervisor.post(
            "/api/v1/orders/" + ids["order"] + "/verify",
            json={"version": response.json()["version"]},
        ).status_code
        == 200
    )
    proposal = {
        "amount_cents": 12900,
        "reason": "尝试超出剩余金额退款，应该拒绝",
        "policy_id": ids["policy"],
        "evidence_ids": [ids["evidence"], ids["consent"]],
    }
    response = staff.post(
        "/api/v1/tickets/" + ids["ticket"] + "/proposals", json=proposal
    )
    assert response.status_code == 409 and response.json()["code"] == "refund_limit"
    proposal["amount_cents"] = 7900
    assert (
        staff.post(
            "/api/v1/tickets/" + ids["ticket"] + "/proposals", json=proposal
        ).status_code
        == 201
    )


def test_customer_withdrawal_resolves_without_financial_action(world):
    ids, login = world
    staff = login("staff")
    supervisor = login("supervisor")
    withdrawal = evidence(staff, ids, "customer_request")
    assert (
        supervisor.post("/api/v1/evidence/" + withdrawal + "/verify").status_code == 200
    )
    result = supervisor.post(
        "/api/v1/tickets/" + ids["ticket"] + "/resolve-without-payment",
        json={
            "outcome": "customer_withdrawn",
            "evidence_id": withdrawal,
            "note": "客户已明确撤回本次售后诉求，已对照原始记录核查",
        },
    )
    assert result.status_code == 200 and result.json()["state"] == "resolved"
    assert (
        staff.post(
            "/api/v1/tickets/" + ids["ticket"] + "/communications",
            json={
                "channel": "测试渠道",
                "content": "已向客户确认本次诉求撤回并结束处理",
            },
        ).status_code
        == 200
    )
    assert staff.post("/api/v1/tickets/" + ids["ticket"] + "/close").status_code == 200
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).refunded_cents == 0
        assert db.scalar(select(Action)) is None


def test_private_file_upload_download_and_tenant_boundary(world, monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings(), "storage_backend", "local")
    monkeypatch.setattr(settings(), "storage_dir", tmp_path)
    ids, login = world
    staff = login("staff")
    outsider = login("outside")
    content = "合成私有证据，保留原始文件内容".encode()
    result = staff.post(
        "/api/v1/tickets/" + ids["ticket"] + "/evidence",
        data={
            "kind": "logistics",
            "title": "私有文本材料",
            "source": "合成测试附件",
            "observed_at": now().isoformat() + "Z",
        },
        files={"file": ("evidence.txt", content, "text/plain")},
    )
    assert result.status_code == 201, result.text
    evidence_id = result.json()["id"]
    download = staff.get("/api/v1/evidence/" + evidence_id + "/file")
    assert download.status_code == 200 and download.content == content
    assert "attachment" in download.headers["content-disposition"]
    assert outsider.get("/api/v1/evidence/" + evidence_id + "/file").status_code == 404
