import json

from app.config import Settings
from app.database import SessionLocal
from app.models import AgentRun, ModelBudget, ModelCall
from app.worker import run_once
from sqlalchemy import select
from test_agent_runtime import provider, start


def test_secret_file_is_loaded_and_never_serialized(tmp_path):
    secret = tmp_path / "model-key"
    secret.write_text("synthetic-private-key\n")
    secret.chmod(0o600)
    cfg = Settings(_env_file=None, llm_api_key_file=secret)
    assert cfg.llm_api_key == "synthetic-private-key"
    assert "synthetic-private-key" not in repr(cfg)
    assert "synthetic-private-key" not in cfg.model_dump_json()


def test_deepseek_request_uses_supported_fields(world, configured, monkeypatch):
    ids, login = world
    monkeypatch.setattr(configured, "llm_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(configured, "llm_model", "deepseek-flash")
    requests = provider(
        monkeypatch,
        [
            {
                "tool": "request_evidence",
                "rationale": "需要补充核验事实",
                "missing_items": ["承运商最新说明"],
            }
        ],
    )
    start(login("staff"), ids)
    run_once()
    assert requests[0]["max_tokens"] == 1600
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert "store" not in requests[0] and "max_completion_tokens" not in requests[0]
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "test-only-key" not in json.dumps(requests)


def test_total_budget_survives_repeated_runs_and_unknown_billing(
    world, configured, monkeypatch
):
    ids, login = world
    monkeypatch.setattr(configured, "llm_total_budget_microusd", 33399)
    requests = provider(
        monkeypatch,
        [
            {
                "tool": "request_evidence",
                "rationale": "需要人工补充证明",
                "missing_items": ["承运商证明"],
            }
        ],
    )
    client = login("staff")
    first = start(client, ids)
    run_once()
    with SessionLocal.begin() as db:
        budget = db.get(ModelBudget, "deployment")
        assert budget.spent_microusd == 200 and budget.reserved_microusd == 0
        budget.reserved_microusd = 100
        db.get(AgentRun, first).active_key = None
    second = start(client, ids)
    run_once()
    assert len(requests) == 1
    with SessionLocal() as db:
        assert db.get(AgentRun, second).error == "total_budget"
        assert db.get(ModelBudget, "deployment").reserved_microusd == 100
        assert len(db.scalars(select(ModelCall)).all()) == 1


def test_agent_cannot_reduce_customer_refund_to_fit_policy(
    world, configured, monkeypatch
):
    ids, login = world
    calls = provider(
        monkeypatch,
        [
            {
                "tool": "draft_refund",
                "rationale": "降低金额以符合政策",
                "amount_cents": 10000,
                "policy_id": ids["policy"],
                "evidence_ids": [ids["evidence"], ids["consent"]],
            },
            {"tool": "finish", "rationale": "金额需要人工与客户核实"},
        ],
    )
    run_id = start(login("staff"), ids)
    run_once()
    with SessionLocal() as db:
        from app.models import Proposal

        assert db.scalar(select(Proposal)) is None
        assert db.get(AgentRun, run_id).state == "human_needed"
    assert len(calls) == 2


def test_budget_reserves_large_unicode_context_before_request(
    world, configured, monkeypatch
):
    from app import agent

    ids, login = world
    monkeypatch.setattr(configured, "llm_total_budget_microusd", 50000)
    monkeypatch.setattr(agent, "make_context", lambda *args: {"material": "汉" * 20000})
    calls = provider(monkeypatch, [])
    run_id = start(login("staff"), ids)
    run_once()
    assert calls == []
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).error == "total_budget"
        assert db.scalar(select(ModelCall)) is None
