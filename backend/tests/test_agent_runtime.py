"""Provider stubs only live in tests. Production never substitutes model output."""

import json
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import select
from app import agent
from app.database import SessionLocal
from app.models import Action, AgentRun, Job, ModelCall, Proposal, RunEvent, now
from app.worker import claim_job, process_agent, run_once


def provider(monkeypatch, decisions):
    original = httpx.Client
    requests = []

    def reply(request):
        data = json.loads(request.content)
        requests.append(data)
        decision = decisions[len(requests) - 1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(decision)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    monkeypatch.setattr(
        agent.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(reply), **kw),
    )
    return requests


def start(client, ids):
    response = client.post("/api/v1/tickets/" + ids["ticket"] + "/runs")
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_planner_uses_scoped_tools_and_stops_at_approval(
    world, configured, monkeypatch
):
    ids, login = world
    staff = login("staff")
    requests = provider(
        monkeypatch,
        [
            {"tool": "read_order", "rationale": "核查订单金额和付款状态"},
            {"tool": "read_evidence", "rationale": "核对当前证据及核验状态"},
            {
                "tool": "search_policies",
                "rationale": "查找适用丢件政策",
                "query": "丢件",
            },
            {
                "tool": "draft_refund",
                "rationale": "承运商确认丢件且金额符合政策，提交人工审批",
                "amount_cents": 12900,
                "policy_id": ids["policy"],
                "evidence_ids": [ids["evidence"], ids["consent"]],
            },
        ],
    )
    run_id = start(staff, ids)
    assert run_once()
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert (
            run.state == "completed"
            and run.call_count == 4
            and run.cost_microusd == 800
        )
        assert (
            db.scalar(select(Proposal).where(Proposal.ticket_id == ids["ticket"])).state
            == "pending"
        )
        assert db.scalar(select(Action)) is None
        assert run.checkpoint["done"] and run.reserved_microusd == 0
    assert len(requests) == 4
    context = json.loads(requests[-1]["messages"][1]["content"])[
        "untrusted_business_data"
    ]
    assert {o["tool"] for o in context["observations"]} == {
        "read_order",
        "read_evidence",
        "search_policies",
    }
    assert "test-only-key" not in json.dumps(context)


def test_invalid_output_keeps_billing_record_and_fails_closed(
    world, configured, monkeypatch
):
    ids, login = world
    staff = login("staff")
    provider(monkeypatch, [{"tool": "execute_refund", "rationale": "直接退款"}])
    run_id = start(staff, ids)
    run_once()
    with SessionLocal() as db:
        call = db.scalar(select(ModelCall).where(ModelCall.run_id == run_id))
        run = db.get(AgentRun, run_id)
        assert call.state == "failed" and call.error == "model_schema_error"
        assert run.cost_microusd == 200 and run.call_count == 1
        assert db.scalar(select(Action)) is None
        assert (
            db.scalar(select(Job).where(Job.resource_id == run_id)).state == "pending"
        )


def test_budget_exhaustion_goes_to_human_without_extra_call(
    world, configured, monkeypatch
):
    ids, login = world
    staff = login("staff")
    configured.llm_max_calls = 1
    requests = provider(
        monkeypatch, [{"tool": "read_order", "rationale": "核对付款事实"}]
    )
    run_id = start(staff, ids)
    run_once()
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run.state == "human_needed" and run.error == "agent_budget"
    assert len(requests) == 1


def test_checkpoint_resumes_tool_without_repeating_model(
    world, configured, monkeypatch
):
    ids, login = world
    staff = login("staff")
    requests = provider(monkeypatch, [])
    run_id = start(staff, ids)
    with SessionLocal.begin() as db:
        run = db.get(AgentRun, run_id)
        run.checkpoint = {
            "phase": "tool",
            "observations": [],
            "decision": {
                "tool": "request_evidence",
                "rationale": "需补充客户明确选择退款的记录",
                "missing_items": ["客户退款意愿"],
            },
        }
    run_once()
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).state == "waiting_input"
    assert requests == []
    # Re-delivery of a terminal/paused job cannot re-run the model or create another task.
    with SessionLocal.begin() as db:
        db.add(Job(tenant_id=ids["tenant"], kind="agent", resource_id=run_id))
    run_once()
    assert requests == []


def test_old_worker_lease_cannot_commit_tools(world, configured):
    ids, login = world
    run_id = start(login("staff"), ids)
    job, token = claim_job()
    with SessionLocal.begin() as db:
        record = db.get(Job, job.id)
        record.lease_until = now() - timedelta(seconds=1)
    replacement, new_token = claim_job(job.id)
    assert new_token != token
    state = {
        "job_id": job.id,
        "lease_token": token,
        "tenant_id": ids["tenant"],
        "run_id": run_id,
        "decision": {"tool": "finish", "rationale": "人工接管调查"},
    }
    with pytest.raises(Exception) as error:
        agent.tool_node(state)
    assert getattr(error.value, "code", None) == "lease_lost"
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).state == "queued"


def test_quality_review_requires_independent_actor_and_reports_denominator(
    world, configured, monkeypatch
):
    ids, login = world
    staff = login("staff")
    supervisor = login("supervisor")
    provider(
        monkeypatch, [{"tool": "finish", "rationale": "当前证据需要人工进一步核实"}]
    )
    run_id = start(staff, ids)
    run_once()
    label = {
        "business_correct": True,
        "evidence_supported": False,
        "handoff_appropriate": True,
        "note": "已对照原始材料，接管正确但证据引用不完整",
    }
    assert (
        supervisor.post("/api/v1/runs/" + run_id + "/review", json=label).status_code
        == 200
    )
    stats = supervisor.get("/api/v1/evaluations").json()
    assert (
        stats["reviewed"] == 1
        and stats["correct"] == 1.0
        and stats["evidence_supported"] == 0.0
    )
    # Latest review counts once; all prior events remain auditable.
    assert (
        supervisor.post("/api/v1/runs/" + run_id + "/review", json=label).status_code
        == 200
    )
    assert supervisor.get("/api/v1/evaluations").json()["reviewed"] == 1
    assert staff.get("/api/v1/runs/" + run_id).json()["cost_microusd"] is None
    assert supervisor.get("/api/v1/runs/" + run_id).json()["cost_microusd"] == 200


def test_manual_progress_prevents_agent_from_overwriting_stage(
    world, configured, monkeypatch
):
    ids, login = world
    staff = login("staff")
    run_id = start(staff, ids)
    with SessionLocal.begin() as db:
        from app.models import Ticket

        db.get(Ticket, ids["ticket"]).state = "waiting_approval"
        db.get(AgentRun, run_id).checkpoint = {
            "phase": "tool",
            "observations": [],
            "decision": {
                "tool": "request_evidence",
                "rationale": "继续补证调查",
                "missing_items": ["额外物流材料"],
            },
        }
    run_once()
    with SessionLocal() as db:
        assert db.get(Ticket, ids["ticket"]).state == "waiting_approval"
        assert db.get(AgentRun, run_id).state == "human_needed"
