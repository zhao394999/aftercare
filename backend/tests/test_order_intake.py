from datetime import timedelta
from uuid import uuid4

from app.database import SessionLocal
from app.models import ImportJob, Order, Ticket, now
from sqlalchemy import func, select


def order_input(external_id="INTAKE-1"):
    return {
        "external_id": external_id,
        "channel": "测试渠道",
        "product": "测试商品",
        "quantity": 2,
        "paid_cents": 5000,
        "carrier_code": "shunfeng",
        "tracking_number": "SF1234567890",
        "placed_at": (now() - timedelta(days=3)).isoformat() + "Z",
    }


def ticket_input():
    return {
        "title": "测试物流停更",
        "issue": "logistics_delay",
        "request": "请核查物流并联系承运商",
        "auto_investigate": False,
    }


def test_normal_order_does_not_create_ticket_and_retries_are_idempotent(world):
    _, login = world
    staff = login("staff")
    body = {"request_id": str(uuid4()), "order": order_input()}
    response = staff.post("/api/v1/orders/intake", json=body)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["ticket"] is None
    assert (
        saved["order"]["quantity"] == 2
        and saved["order"]["tracking_number"] == "SF1234567890"
    )
    assert (
        staff.post("/api/v1/orders/intake", json=body).json()["order"]["id"]
        == saved["order"]["id"]
    )
    assert (
        staff.post(
            "/api/v1/orders/intake", json={**body, "order": order_input("DIFFERENT")}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Ticket.id))) == 1
        assert db.scalar(select(func.count(Order.id))) == 2


def test_order_and_ticket_save_atomically_and_preserve_logistics(world):
    _, login = world
    staff = login("staff")
    body = {
        "request_id": str(uuid4()),
        "order": order_input(),
        "ticket": ticket_input(),
    }
    result = staff.post("/api/v1/orders/intake", json=body)
    assert result.status_code == 201, result.text
    saved = result.json()
    detail = staff.get("/api/v1/tickets/" + saved["ticket"]["id"]).json()
    assert detail["order"]["tracking_number"] == "SF1234567890"
    assert (
        detail["order"]["quantity"] == 2
        and detail["request"] == ticket_input()["request"]
    )
    assert (
        staff.post("/api/v1/orders/intake", json=body).json()["ticket"]["id"]
        == detail["id"]
    )
    filtered = staff.get(
        "/api/v1/tickets", params={"intake_id": body["request_id"]}
    ).json()
    assert filtered["total"] == 1
    assert (
        staff.get("/api/v1/orders", params={"ids": saved["order"]["id"]}).json()[
            "items"
        ][0]["ticket_count"]
        == 1
    )


def test_intake_failure_rolls_back_the_order(world, monkeypatch):
    from app import domain
    from app.security import require

    _, login = world

    def reject(*args, **kwargs):
        require(False, "test_failure", "模拟工单保存失败", 409)

    monkeypatch.setattr(domain, "create_ticket", reject)
    staff = login("staff")
    result = staff.post(
        "/api/v1/orders/intake",
        json={
            "request_id": str(uuid4()),
            "order": order_input(),
            "ticket": ticket_input(),
        },
    )
    assert result.status_code == 409
    assert staff.get("/api/v1/orders?q=INTAKE-1").json()["total"] == 0


def existing_body(staff, order_id):
    order = staff.get("/api/v1/orders", params={"ids": order_id}).json()["items"][0]
    from app.schemas import OrderInput

    return {
        "request_id": str(uuid4()),
        "existing_order_id": order_id,
        "order_version": order["version"],
        "order": {k: v for k, v in order.items() if k in OrderInput.model_fields},
        "ticket": ticket_input(),
    }


def test_existing_order_updates_once_and_preserves_unchanged_snapshot(world):
    ids, login = world
    staff = login("staff")
    body = existing_body(staff, ids["order"])
    same = staff.post("/api/v1/orders/intake", json=body)
    assert same.status_code == 201, same.text
    assert same.json()["order"]["version"] == 1 and same.json()["order"]["verified_at"]
    body = {
        **body,
        "request_id": str(uuid4()),
        "order": {
            **body["order"],
            "carrier_code": "shunfeng",
            "tracking_number": "SF1234567890",
        },
    }
    result = staff.post("/api/v1/orders/intake", json=body)
    assert result.status_code == 201, result.text
    assert (
        result.json()["order"]["version"] == 2
        and result.json()["order"]["verified_at"] is None
    )
    assert (
        staff.post(
            "/api/v1/orders/intake", json={**body, "request_id": str(uuid4())}
        ).status_code
        == 409
    )
    assert (
        login("outside")
        .post("/api/v1/orders/intake", json={**body, "request_id": str(uuid4())})
        .status_code
        == 404
    )


def test_batch_validates_all_rows_and_does_not_duplicate_retries(world):
    ids, login = world
    staff = login("staff")
    second = staff.post("/api/v1/orders", json=order_input()).json()
    rows = [
        dict(ticket_input(), order_id=ids["order"], order_version=1),
        dict(
            ticket_input(),
            order_id=second["id"],
            order_version=99,
            request="第二位客户只要求查件",
        ),
    ]
    batch_id = str(uuid4())
    body = {"request_id": batch_id, "items": rows}
    assert staff.post("/api/v1/tickets/batch", json=body).status_code == 409
    assert (
        staff.get("/api/v1/tickets", params={"intake_id": batch_id}).json()["total"]
        == 0
    )
    rows[1]["order_version"] = 1
    result = staff.post("/api/v1/tickets/batch", json=body)
    assert result.status_code == 201, result.text
    assert len(result.json()["items"]) == 2
    replay = staff.post("/api/v1/tickets/batch", json=body)
    assert [t["id"] for t in replay.json()["items"]] == [
        t["id"] for t in result.json()["items"]
    ]
    filtered = staff.get("/api/v1/tickets", params={"intake_id": batch_id}).json()
    assert filtered["total"] == 2
    assert {t["request"] for t in filtered["items"]} == {
        rows[0]["request"],
        rows[1]["request"],
    }
    assert (
        login("outside")
        .post("/api/v1/tickets/batch", json={**body, "request_id": str(uuid4())})
        .status_code
        == 404
    )
    assert (
        staff.post(
            "/api/v1/tickets/batch",
            json={"request_id": str(uuid4()), "items": [rows[0]] * 2},
        ).status_code
        == 422
    )
    assert (
        staff.post(
            "/api/v1/tickets/batch",
            json={"request_id": str(uuid4()), "items": [rows[0]] * 51},
        ).status_code
        == 422
    )


def test_completed_import_filter_includes_preexisting_duplicates_without_tickets(world):
    ids, login = world
    with SessionLocal.begin() as db:
        job = ImportJob(
            tenant_id=ids["tenant"],
            filename="orders.csv",
            created_by=ids["users"]["staff"],
            content_hash="x" * 64,
            state="completed",
            rows=[{"channel": "商家导入", "external_id": "ORDER-1"}],
        )
        db.add(job)
        db.flush()
        key = job.id
    staff = login("staff")
    result = staff.get("/api/v1/orders", params={"import_id": key})
    assert result.status_code == 200 and result.json()["total"] == 1
    assert result.json()["items"][0]["id"] == ids["order"]
    assert (
        login("outside").get("/api/v1/orders", params={"import_id": key}).status_code
        == 404
    )


def test_batch_order_updates_and_tickets_roll_back_together(world, monkeypatch):
    from app import domain
    from app.security import require

    ids, login = world
    staff = login("staff")
    second = staff.post("/api/v1/orders", json=order_input()).json()
    first = existing_body(staff, ids["order"])
    body = {
        "request_id": str(uuid4()),
        "items": [
            dict(
                ticket_input(),
                order_id=ids["order"],
                order_version=1,
                order={**first["order"], "quantity": 3},
            ),
            dict(ticket_input(), order_id=second["id"], order_version=1),
        ],
    }
    original = domain.create_ticket

    def reject_second(db, actor, payload, **kwargs):
        require(
            payload.order_id != second["id"], "test_failure", "模拟第二笔建单失败", 409
        )
        return original(db, actor, payload, **kwargs)

    monkeypatch.setattr(domain, "create_ticket", reject_second)
    assert staff.post("/api/v1/tickets/batch", json=body).status_code == 409
    rolled_back = staff.get("/api/v1/orders", params={"ids": ids["order"]}).json()[
        "items"
    ][0]
    assert rolled_back["quantity"] == first["order"]["quantity"]
    assert rolled_back["version"] == 1 and rolled_back["verified_at"]
    assert (
        staff.get("/api/v1/tickets", params={"intake_id": body["request_id"]}).json()[
            "total"
        ]
        == 0
    )
    monkeypatch.setattr(domain, "create_ticket", original)
    response = staff.post("/api/v1/tickets/batch", json=body)
    assert response.status_code == 201, response.text
    updated = staff.get("/api/v1/tickets/" + response.json()["items"][0]["id"]).json()[
        "order"
    ]
    assert (
        updated["quantity"] == 3
        and updated["version"] == 2
        and updated["verified_at"] is None
    )
    assert staff.post("/api/v1/tickets/batch", json=body).status_code == 201
