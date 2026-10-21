from datetime import timedelta

from app.database import SessionLocal
from app.knowledge import retrieve
from app.main import app
from app.models import Action, AgentRun, ModelCall, Policy, Proposal, Ticket, User, now
from app.worker import run_once
from conftest import ORIGIN, PASSWORD
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_agent_runtime import provider


def test_registration_creates_isolated_workspace_and_preserves_existing_identity(world):
    ids, _login = world
    with TestClient(app) as client:
        body = {
            "email": "NEW@example.test",
            "password": PASSWORD,
            "name": "新主管",
            "workspace": "新商家",
        }
        assert client.post("/api/v1/auth/register", json=body).status_code == 403
        result = client.post(
            "/api/v1/auth/register", json=body, headers={"origin": ORIGIN}
        )
        assert result.status_code == 201, result.text
        profile = result.json()
        assert profile["role"] == "supervisor"
        assert profile["tenant"]["id"] != ids["tenant"]
        assert profile["tenant"]["is_demo"] is False
        assert client.get("/api/v1/tickets").json()["total"] == 0
        assert (
            client.post(
                "/api/v1/auth/register", json=body, headers={"origin": ORIGIN}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/auth/register",
                json={**body, "email": "staff@test.local"},
                headers={"origin": ORIGIN},
            ).status_code
            == 409
        )
    with SessionLocal() as db:
        assert (
            db.scalar(select(User).where(User.email == "staff@test.local")).id
            == ids["users"]["staff"]
        )


def test_personal_queue_matches_cards_and_excludes_other_owners(world):
    ids, login = world
    with SessionLocal.begin() as db:
        db.add(
            Ticket(
                tenant_id=ids["tenant"],
                order_id=ids["order"],
                number="OTHER-OWNER",
                title="另一个负责人的工单",
                request="测试",
                owner_id=ids["users"]["admin"],
                due_at=now(),
            )
        )
    staff = login("staff")
    assert staff.get("/api/v1/overview?scope=mine").json()["open"] == 1
    rows = staff.get("/api/v1/tickets?scope=mine&state=open").json()
    assert rows["total"] == 1 and rows["items"][0]["id"] == ids["ticket"]
    assert staff.get("/api/v1/tickets").json()["total"] == 2
    assert login("supervisor").get("/api/v1/overview?scope=mine").json()["open"] == 0


def test_two_roles_can_approve_and_claim_without_self_approval(approved):
    ids, staff, supervisor, _, _proposal, action = approved
    assert supervisor.get("/api/v1/overview?scope=mine").json()["waiting_approval"] == 0
    claim = staff.post(
        f"/api/v1/actions/{action['id']}/claim",
        json={
            "order_version": 1,
            "checked_at": now().isoformat() + "Z",
            "current_state_confirmed": True,
        },
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["claimed_by"] == ids["users"]["staff"]
    assert (
        supervisor.post(
            f"/api/v1/actions/{action['id']}/claim",
            json={
                "order_version": 1,
                "checked_at": now().isoformat() + "Z",
                "current_state_confirmed": True,
            },
        ).status_code
        == 403
    )
    proof = staff.post(
        f"/api/v1/tickets/{ids['ticket']}/evidence",
        data={
            "kind": "execution_receipt",
            "title": "合成退款凭证",
            "body": "外部合成流水已退款129元",
            "source": "合成验收",
            "observed_at": now().isoformat() + "Z",
        },
    ).json()["id"]
    assert supervisor.post(f"/api/v1/evidence/{proof}/verify").status_code == 200
    receipt = staff.post(
        f"/api/v1/actions/{action['id']}/receipt",
        json={
            "external_ref": "TWO-ROLE-TEST",
            "external_state": "succeeded",
            "actual_cents": 12900,
            "evidence_id": proof,
        },
    )
    assert receipt.status_code == 200, receipt.text
    assert (
        supervisor.get("/api/v1/tickets?scope=mine&state=waiting_verification").json()[
            "total"
        ]
        == 1
    )
    assert (
        supervisor.post(
            f"/api/v1/actions/{action['id']}/verify",
            json={"confirmed": True, "note": "合成凭证的金额和终态已独立核实"},
        ).status_code
        == 200
    )


def test_supervisor_queue_excludes_own_pending_proposal(world):
    ids, login = world
    staff, supervisor = login("staff"), login("supervisor")
    proposal = staff.post(
        f"/api/v1/tickets/{ids['ticket']}/proposals",
        json={
            "amount_cents": 12900,
            "reason": "合成丢件确认，客户明确申请退款",
            "policy_id": ids["policy"],
            "evidence_ids": [ids["evidence"], ids["consent"]],
        },
    ).json()
    assert supervisor.get("/api/v1/overview?scope=mine").json()["waiting_approval"] == 1
    with SessionLocal.begin() as db:
        db.get(Proposal, proposal["id"]).created_by = ids["users"]["supervisor"]
    assert supervisor.get("/api/v1/overview?scope=mine").json()["waiting_approval"] == 0


def test_retrieval_filters_tenant_lifecycle_time_channel_and_old_versions(world):
    ids, _ = world
    with SessionLocal.begin() as db:
        for state, tenant, channel, start, end, title in [
            ("draft", ids["tenant"], "*", now() - timedelta(days=2), None, "草稿"),
            ("retired", ids["tenant"], "*", now() - timedelta(days=2), None, "停用"),
            (
                "published",
                ids["other"],
                "*",
                now() - timedelta(days=2),
                None,
                "其他租户",
            ),
            (
                "published",
                ids["tenant"],
                "其他渠道",
                now() - timedelta(days=2),
                None,
                "其他渠道",
            ),
            ("published", ids["tenant"], "*", now() + timedelta(days=2), None, "未来"),
            (
                "published",
                ids["tenant"],
                "*",
                now() - timedelta(days=2),
                now() - timedelta(days=1),
                "过期",
            ),
        ]:
            db.add(
                Policy(
                    tenant_id=tenant,
                    title=title,
                    body="丢件退款政策",
                    state=state,
                    channel=channel,
                    effective_from=start,
                    effective_until=end,
                    content_hash="x" * 64,
                )
            )
        newer = Policy(
            tenant_id=ids["tenant"],
            title="测试政策",
            version=2,
            body="丢件退款需要人工审批，不能直接退款。",
            state="published",
            effective_from=now() - timedelta(days=1),
            content_hash="z" * 64,
        )
        db.add(newer)
        db.flush()
        hits = retrieve(db, ids["tenant"], "丢件退款", "*", now())
        assert {h["id"] for h in hits} == {newer.id}
        assert hits[0]["version"] == 2
        assert retrieve(db, ids["tenant"], "zzznomatch", "*", now()) == []


def test_knowledge_uses_real_runtime_contract_billing_and_citations(
    world, configured, monkeypatch
):
    ids, login = world
    staff = login("staff")
    citation = ids["policy"] + ":0"
    requests = provider(
        monkeypatch,
        [
            {
                "tool": "answer_knowledge",
                "rationale": "依据已发布政策回答",
                "answer": "确认丢件后可提出退款，必须人工审批。[1]",
                "citations": [citation],
            }
        ],
    )
    result = staff.post("/api/v1/knowledge/ask", json={"question": "丢件如何退款"})
    assert result.status_code == 202, result.text
    run_id = result.json()["id"]
    assert run_once()
    response = staff.get("/api/v1/runs/" + run_id).json()
    assert response["state"] == "completed"
    assert (
        response["checkpoint"]["knowledge_answer"]["sources"][0]["id"] == ids["policy"]
    )
    assert response["call_count"] == 1 and response["input_tokens"] == 100
    assert len(requests) == 1
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).cost_microusd == 200
        assert (
            db.scalar(select(ModelCall).where(ModelCall.run_id == run_id)).state
            == "succeeded"
        )
        assert db.scalar(select(Action)) is None and db.scalar(select(Proposal)) is None
    assert all(r["id"] != run_id for r in staff.get("/api/v1/runs").json())
    assert staff.get("/api/v1/evaluations").json()["runs"] == 0
    assert login("outside").get("/api/v1/runs/" + run_id).status_code == 404


def test_knowledge_rejects_invented_citations_and_cannot_execute_actions(
    world, configured, monkeypatch
):
    _, login = world
    staff = login("staff")
    bad = {
        "tool": "answer_knowledge",
        "rationale": "测试无效引用",
        "answer": "虚构回答",
        "citations": ["invented"],
    }
    provider(monkeypatch, [bad, bad, bad])
    result = staff.post("/api/v1/knowledge/ask", json={"question": "丢件退款"})
    assert result.status_code == 202
    assert run_once()
    run = staff.get("/api/v1/runs/" + result.json()["id"]).json()
    assert run["state"] == "human_needed"
    assert "knowledge_answer" not in run["checkpoint"]
    assert (
        staff.post("/api/v1/knowledge/ask", json={"question": "zzznomatch"}).status_code
        == 422
    )


def test_knowledge_blocks_other_tools_before_external_calls(
    world, configured, monkeypatch
):
    _, login = world
    from app import logistics

    def forbidden(*args, **kwargs):
        raise AssertionError("knowledge questions must never invoke logistics")

    monkeypatch.setattr(logistics, "query_logistics", forbidden)
    bad = {"tool": "query_logistics", "rationale": "尝试越界调用"}
    provider(monkeypatch, [bad, bad, bad])
    staff = login("staff")
    result = staff.post("/api/v1/knowledge/ask", json={"question": "丢件退款"})
    assert run_once()
    run = staff.get("/api/v1/runs/" + result.json()["id"]).json()
    assert run["state"] == "human_needed" and run["error"] == "knowledge_read_only"


def test_pending_evidence_goes_to_supervisor_queue(world):
    ids, login = world
    staff, supervisor = login("staff"), login("supervisor")
    result = staff.post(
        f"/api/v1/tickets/{ids['ticket']}/evidence",
        data={
            "kind": "logistics",
            "title": "待核查轨迹",
            "body": "合成物流轨迹资料",
            "source": "合成资料",
            "observed_at": now().isoformat() + "Z",
        },
    )
    assert result.status_code == 201
    assert supervisor.get("/api/v1/overview?scope=mine").json()["evidence_review"] == 1
    assert (
        supervisor.get("/api/v1/tickets?scope=mine&state=evidence_review").json()[
            "total"
        ]
        == 1
    )
    assert (
        supervisor.post(
            "/api/v1/evidence/" + result.json()["id"] + "/verify"
        ).status_code
        == 200
    )
    assert supervisor.get("/api/v1/overview?scope=mine").json()["evidence_review"] == 0
