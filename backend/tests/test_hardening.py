import json
import subprocess
from datetime import timedelta

import httpx
import pytest
from app.config import settings
from app.database import SessionLocal
from app.importing import parse_csv
from app.models import (
    Action,
    AgentRun,
    AuditEvent,
    Evidence,
    Job,
    Order,
    Policy,
    Proposal,
    now,
    uid,
)
from app.security import DomainError
from app.worker import claim_job, run_once
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_agent_runtime import provider, start
from test_business_boundaries import claim, evidence


def waiting_run(ids):
    with SessionLocal.begin() as db:
        run = AgentRun(
            tenant_id=ids["tenant"],
            ticket_id=ids["ticket"],
            requester_id=ids["users"]["staff"],
            active_key=ids["ticket"],
            state="waiting_input",
            model="fixture-model",
            budget_microusd=1000000,
        )
        db.add(run)
        db.flush()
        return run.id


def test_success_cannot_be_reconciled_as_not_executed(approved):
    ids, _, supervisor, executor, _, action = approved
    claim(executor, ids, action)
    receipt = evidence(executor, ids)
    executor.post(
        f"/api/v1/actions/{action['id']}/receipt",
        json={
            "external_ref": "SIM-PARTIAL-1",
            "external_state": "succeeded",
            "actual_cents": 5000,
            "evidence_id": receipt,
        },
    )
    proof = evidence(supervisor, ids, "reconciliation")
    supervisor.post(f"/api/v1/evidence/{proof}/verify")
    result = supervisor.post(
        f"/api/v1/actions/{action['id']}/reconcile",
        json={
            "confirmed_not_executed": True,
            "evidence_id": proof,
            "note": "合成演练：与已登记成功金额矛盾的未执行确认",
        },
    )
    assert result.status_code == 409
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 12900


def test_disabled_executor_handoff_preserves_reservation(approved, world):
    ids, _, supervisor, executor, _, action = approved
    admin = world[1]("admin")
    claim(executor, ids, action)
    admin.put(
        f"/api/v1/members/{ids['users']['executor']}",
        json={"role": "executor", "enabled": False},
    )
    proof = evidence(supervisor, ids, "reconciliation")
    supervisor.post(f"/api/v1/evidence/{proof}/verify")
    body = {
        "executor_id": ids["users"]["admin"],
        "evidence_id": proof,
        "note": "合成交接：原经办人已离职，已联系核对外部操作进度",
    }
    result = supervisor.post(f"/api/v1/actions/{action['id']}/handoff", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "reconciling"
    assert result.json()["claimed_by"] == ids["users"]["admin"]
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 12900
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.event == "action.handed_off")
        )
        assert audit.details["previous_executor_id"] == ids["users"]["executor"]
    receipt = evidence(admin, ids)
    result = admin.post(
        f"/api/v1/actions/{action['id']}/receipt",
        json={
            "external_ref": "SIM-HANDOFF-1",
            "external_state": "failed",
            "actual_cents": 0,
            "evidence_id": receipt,
        },
    )
    assert result.status_code == 200
    result = supervisor.post(
        f"/api/v1/actions/{action['id']}/reconcile",
        json={
            "confirmed_not_executed": True,
            "evidence_id": proof,
            "note": "合成演练：独立核查外部未执行，结束原动作",
        },
    )
    assert result.status_code == 200, result.text


def test_invitation_and_login_share_account_throttle(world):
    _, login = world
    admin = login("admin")
    invitation = admin.post(
        "/api/v1/invitations", json={"email": "staff@test.local", "role": "staff"}
    ).json()
    token = invitation["url"].rsplit("/", 1)[1]
    client = TestClient(admin.app)
    for _ in range(8):
        assert (
            client.post(
                "/api/v1/auth/login",
                headers={"origin": "http://127.0.0.1:5173"},
                json={
                    "email": "staff@test.local",
                    "password": "Wrong-fixture-password",
                },
            ).status_code
            == 401
        )
    result = client.post(
        "/api/v1/invitations/accept",
        headers={"origin": "http://127.0.0.1:5173"},
        json={"token": token, "name": "合成用户", "password": "Wrong-fixture-password"},
    )
    assert result.status_code == 429
    client.close()


def test_sensitive_invitation_url_is_excluded_from_traces():
    from app.telemetry import SENSITIVE_TRACE_URLS
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    tracer = TracerProvider()
    tracer.add_span_processor(SimpleSpanProcessor(exporter))
    probe = FastAPI()

    @probe.get("/api/v1/invitations/{token}")
    def invitation(token: str):
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(
        probe, tracer_provider=tracer, excluded_urls=SENSITIVE_TRACE_URLS
    )
    token = "synthetic-invite-secret-123456"
    with TestClient(probe) as client:
        assert client.get("/api/v1/invitations/" + token).status_code == 200
    assert token not in str([dict(s.attributes) for s in exporter.get_finished_spans()])
    tracer.shutdown()


def test_all_model_context_fields_are_redacted(world, configured, monkeypatch):
    ids, login = world
    with SessionLocal.begin() as db:
        db.get(Evidence, ids["evidence"]).title = "13800138000 audit@example.invalid"
        db.get(Order, ids["order"]).external_id = "13800138000"
    requests = provider(
        monkeypatch,
        [
            {"tool": "read_order", "rationale": "核对订单事实"},
            {"tool": "read_evidence", "rationale": "核对证据资料"},
            {"tool": "finish", "rationale": "交由人工继续核实"},
        ],
    )
    start(login("staff"), ids)
    run_once()
    sent = json.dumps([r["messages"] for r in requests])
    assert "13800138000" not in sent and "audit@example.invalid" not in sent


def test_resume_obeys_concurrency_and_pause(world, configured):
    ids, login = world
    staff = login("staff")
    target = waiting_run(ids)
    with SessionLocal.begin() as db:
        for _ in range(10):
            db.add(
                AgentRun(
                    tenant_id=ids["tenant"],
                    ticket_id=ids["ticket"],
                    requester_id=ids["users"]["staff"],
                    state="queued",
                    model="fixture-model",
                    budget_microusd=1000000,
                )
            )
    assert staff.post(f"/api/v1/runs/{target}/resume").status_code == 429
    from app.models import Tenant

    with SessionLocal.begin() as db:
        db.get(Tenant, ids["tenant"]).config = {"agent_enabled": False}
    assert staff.post(f"/api/v1/runs/{target}/resume").status_code == 409


def test_expired_job_retries_are_bounded(world, configured):
    ids, login = world
    run_id = start(login("staff"), ids)
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.resource_id == run_id))
        job.state = "running"
        job.attempts = 3
        job.lease_token = uid()
        job.lease_until = now() - timedelta(seconds=1)
        jid = job.id
    assert claim_job(jid) is None
    with SessionLocal() as db:
        assert db.get(Job, jid).state == "failed"
        assert db.get(AgentRun, run_id).state == "human_needed"


def test_daily_model_budget_covers_successive_runs(world, configured, monkeypatch):
    ids, login = world
    monkeypatch.setattr(configured, "llm_tenant_daily_budget_microusd", 33399)
    staff = login("staff")
    calls = provider(
        monkeypatch, [{"tool": "finish", "rationale": "合成材料交由人工核查"}]
    )
    start(staff, ids)
    run_once()
    second = start(staff, ids)
    run_once()
    assert len(calls) == 1
    with SessionLocal() as db:
        assert db.get(AgentRun, second).error == "tenant_budget"


@pytest.mark.parametrize(
    "slow", [False, True], ids=["oversize-response", "slow-response"]
)
def test_model_response_has_size_and_total_time_bounds(
    world, configured, monkeypatch, slow
):
    from types import SimpleNamespace

    from app import agent
    from app.models import ModelCall

    ids, login = world
    clock = [0]

    class ResponseBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{"
            if slow:
                clock[0] = 60
            yield b"x" * (1 if slow else 128 * 1024)

    original = httpx.Client
    monkeypatch.setattr(agent, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(
        agent.httpx,
        "Client",
        lambda **kw: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=ResponseBody())
            ),
            **kw,
        ),
    )
    run_id = start(login("staff"), ids)
    run_once()
    with SessionLocal() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.run_id == run_id))
        assert call.error == "model_response_limit"
        assert call.state == "usage_unknown" and call.reserved_microusd > 0


def test_handoff_rejects_unverified_proof_and_wrong_roles(approved, world):
    ids, staff, supervisor, executor, _, action = approved
    claim(executor, ids, action)
    proof = evidence(supervisor, ids, "reconciliation")
    body = {
        "executor_id": ids["users"]["admin"],
        "evidence_id": proof,
        "note": "合成交接：必须先人工核验外部进度证明",
    }
    endpoint = f"/api/v1/actions/{action['id']}/handoff"
    assert staff.post(endpoint, json=body).status_code == 403
    assert supervisor.post(endpoint, json=body).status_code == 409
    supervisor.post(f"/api/v1/evidence/{proof}/verify")
    body["executor_id"] = ids["users"]["supervisor"]
    assert supervisor.post(endpoint, json=body).status_code == 422
    with SessionLocal() as db:
        assert db.get(Order, ids["order"]).reserved_cents == 12900
        assert db.get(Action, action["id"]).claimed_by == ids["users"]["executor"]


def test_dispatch_does_not_flood_or_delay_claims(world, monkeypatch):
    from app.celery_app import dispatch, process

    ids, _ = world
    with SessionLocal.begin() as db:
        job = Job(tenant_id=ids["tenant"], kind="import", resource_id=uid())
        db.add(job)
        db.flush()
        jid = job.id
    delivered = []
    monkeypatch.setattr(process, "delay", lambda job_id: delivered.append(job_id))
    dispatch()
    dispatch()
    assert delivered == [jid]
    assert claim_job(jid) is not None


def test_worker_outage_is_visible_and_scoped(world):
    from app.worker import heartbeat

    _, login = world
    supervisor = login("supervisor")
    assert supervisor.get("/health/workers").status_code == 503
    assert supervisor.get("/api/v1/operations").json()["worker_available"] is False
    heartbeat()
    assert supervisor.get("/health/workers").status_code == 200
    assert supervisor.get("/api/v1/operations").json()["worker_available"] is True
    assert login("staff").get("/api/v1/operations").status_code == 403


def test_invalid_csv_datetime_is_row_error():
    rows, errors = parse_csv(
        "订单号,商品名称,数量,实付金额,订单状态,下单时间\nSIM-TZ,合成商品,1,129,已发货,错误日期\n".encode()
    )
    assert not rows and errors[0]["line"] == 2


def test_pdf_parsing_works_in_child_process():
    import io

    from app.storage import validate_file
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    mime, text, digest = validate_file(buffer.getvalue(), "synthetic.pdf")
    assert mime == "application/pdf" and text == "" and len(digest) == 64


def test_pdf_concurrency_is_bounded():
    from app.storage import PDF_PARSERS, validate_file

    assert PDF_PARSERS.acquire(blocking=False)
    try:
        with pytest.raises(DomainError) as error:
            validate_file(b"%PDF-synthetic", "synthetic.pdf")
        assert error.value.code == "pdf_busy"
    finally:
        PDF_PARSERS.release()


def test_order_pages_and_search_reach_old_orders(world):
    ids, login = world
    with SessionLocal.begin() as db:
        for n in range(205):
            db.add(
                Order(
                    tenant_id=ids["tenant"],
                    external_id=f"SIM-PAGE-{n}",
                    product="合成分页商品",
                    paid_cents=100,
                    placed_at=now(),
                )
            )
    staff = login("staff")
    page = staff.get("/api/v1/orders?page=2&page_size=50").json()
    assert page["total"] == 206 and len(page["items"]) == 50
    found = staff.get("/api/v1/orders?q=ORDER-1").json()
    assert found["total"] == 1 and found["items"][0]["id"] == ids["order"]


def test_model_changes_stop_resumed_run(world, configured, monkeypatch):
    ids, login = world
    staff = login("staff")
    run_id = start(staff, ids)
    configured.llm_model = "changed-model"
    calls = provider(monkeypatch, [])
    run_once()
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).state == "human_needed"
    assert not calls


def test_resuming_invalidates_previous_quality_review(world, configured):
    ids, login = world
    run_id = waiting_run(ids)
    staff, supervisor = login("staff"), login("supervisor")
    label = {
        "business_correct": True,
        "evidence_supported": True,
        "handoff_appropriate": True,
        "note": "合成演练：当前补证处理符合预期业务规则",
    }
    assert (
        supervisor.post(f"/api/v1/runs/{run_id}/review", json=label).status_code == 200
    )
    assert supervisor.get("/api/v1/evaluations").json()["reviewed"] == 1
    assert staff.post(f"/api/v1/runs/{run_id}/resume").status_code == 202
    assert supervisor.get(f"/api/v1/runs/{run_id}/review").json()["review"] is None
    assert supervisor.get("/api/v1/evaluations").json()["reviewed"] == 0


def test_csv_naive_time_uses_business_timezone():
    rows, errors = parse_csv(
        (
            "订单号,商品名称,数量,实付金额,订单状态,下单时间\n"
            "SIM-TZ,合成商品,1,129,已发货,2026-10-18 12:00:00\n"
        ).encode()
    )
    assert not errors
    assert rows[0]["placed_at"] == "2026-10-18T04:00:00Z"


def test_order_changes_keep_before_and_after(world):
    ids, login = world
    staff = login("staff")
    order = staff.get("/api/v1/orders").json()["items"][0]
    keys = [
        "external_id",
        "channel",
        "product",
        "quantity",
        "paid_cents",
        "refunded_cents",
        "customer",
        "status",
        "placed_at",
        "shipped_at",
        "promised_at",
        "version",
    ]
    payload = {k: order[k] for k in keys}
    payload["product"] = "更新后的合成商品"
    assert staff.put(f"/api/v1/orders/{ids['order']}", json=payload).status_code == 200
    with SessionLocal() as db:
        change = db.scalar(
            select(AuditEvent).where(AuditEvent.event == "order.snapshot_updated")
        )
        assert change.details["before"]["product"] == "测试商品"
        assert change.details["after"]["product"] == "更新后的合成商品"


def test_refund_proposal_freezes_order_facts(approved):
    _, _, _, _, proposal, _ = approved
    with SessionLocal() as db:
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.event == "proposal.submitted")
        )
        assert audit.details["order_snapshot"]["paid_cents"] == 12900
        assert audit.details["payload_hash"] == proposal["payload_hash"]


def test_retrieval_keeps_required_evidence_and_valid_policy(
    world, configured, monkeypatch
):
    ids, login = world
    with SessionLocal.begin() as db:
        for n in range(35):
            db.add(
                Evidence(
                    tenant_id=ids["tenant"],
                    ticket_id=ids["ticket"],
                    kind="logistics",
                    title="无关合成轨迹",
                    body="仅为物流轨迹",
                    source="合成",
                    observed_at=now(),
                    verified_at=now(),
                    uploaded_by=ids["users"]["staff"],
                    content_hash="c" * 64,
                )
            )
            db.add(
                Policy(
                    tenant_id=ids["tenant"],
                    title="已过适用期政策",
                    body="合成已过适用期政策",
                    version=n + 2,
                    state="published",
                    effective_from=now() - timedelta(days=100),
                    effective_until=now() - timedelta(days=10),
                    content_hash="d" * 64,
                )
            )
    calls = provider(
        monkeypatch,
        [
            {"tool": "read_evidence", "rationale": "核对丢件和客户意愿"},
            {"tool": "search_policies", "rationale": "查找订单适用政策"},
            {"tool": "finish", "rationale": "交由人工继续确认"},
        ],
    )
    start(login("staff"), ids)
    run_once()
    context = json.loads(calls[-1]["messages"][1]["content"])["untrusted_business_data"]
    observations = {o["tool"]: o["result"] for o in context["observations"]}
    assert {ids["evidence"], ids["consent"]} <= {
        e["id"] for e in observations["read_evidence"]["items"]
    }
    assert ids["policy"] in {p["id"] for p in observations["search_policies"]["items"]}


def test_pdf_parser_timeout_is_safe(monkeypatch):
    from app.storage import validate_file

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic-parser", 10)

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(DomainError) as error:
        validate_file(b"%PDF-synthetic", "synthetic.pdf")
    assert error.value.code == "pdf_limit"


def test_uploaded_file_is_removed_when_transaction_fails(world, monkeypatch, tmp_path):
    from app import domain

    ids, login = world
    staff = login("staff")
    monkeypatch.setattr(settings(), "storage_dir", tmp_path)

    def reject(*args, **kwargs):
        raise DomainError("synthetic_conflict", "合成事务冲突")

    monkeypatch.setattr(domain, "audit", reject)
    result = staff.post(
        f"/api/v1/tickets/{ids['ticket']}/evidence",
        data={
            "kind": "logistics",
            "title": "合成文件",
            "source": "合成测试",
            "body": "合成材料摘要",
            "observed_at": now().isoformat() + "Z",
        },
        files={"file": ("synthetic.txt", b"synthetic evidence", "text/plain")},
    )
    assert result.status_code == 409
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_execution_pages_preserve_old_open_work(approved):
    ids, staff, _, _, _, action = approved
    with SessionLocal.begin() as db:
        template = db.get(Proposal, action["proposal_id"])
        for _ in range(205):
            proposal = Proposal(
                **{
                    column.name: getattr(template, column.name)
                    for column in Proposal.__table__.columns
                    if column.name != "id"
                }
            )
            db.add(proposal)
            db.flush()
            db.add(
                Action(
                    tenant_id=ids["tenant"],
                    proposal_id=proposal.id,
                    ticket_id=ids["ticket"],
                    order_id=ids["order"],
                    amount_cents=100,
                    state="cancelled",
                )
            )
    result = staff.get("/api/v1/executions").json()
    assert result["total"] == 1 and result["items"][0]["id"] == action["id"]
