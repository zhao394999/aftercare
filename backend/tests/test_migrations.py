import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_worker_migration_preserves_existing_business_data(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "migration.db"
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite:///" + str(path),
        "APP_ENV": "development",
        "PYTHONPATH": str(root / "backend"),
    }

    def upgrade(revision):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "backend/alembic.ini",
                "upgrade",
                revision,
            ],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
        )

    upgrade("20261018_ledger")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO tenants(id,name,is_demo,enabled,config,created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("synthetic-tenant", "合成迁移验收", 0, 1, "{}", "2026-10-18 00:00:00"),
        )
        db.execute(
            "INSERT INTO orders(id,tenant_id,external_id,channel,product,quantity,"
            "paid_cents,refunded_cents,reserved_cents,customer,status,placed_at,"
            "version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "synthetic-order",
                "synthetic-tenant",
                "SIM-MIGRATION",
                "合成渠道",
                "合成商品",
                1,
                10000,
                2000,
                3000,
                "合成客户",
                "shipped",
                "2026-10-18 00:00:00",
                1,
                "2026-10-18 00:00:00",
                "2026-10-18 00:00:00",
            ),
        )
        db.execute(
            "INSERT INTO tickets(id,tenant_id,number,order_id,title,request,issue,state,"
            "owner_id,version,evidence_version,due_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old-ticket",
                "synthetic-tenant",
                "OLD-TICKET",
                "synthetic-order",
                "迁移前工单",
                "保留原有客户诉求",
                "logistics_delay",
                "waiting_evidence",
                "synthetic-owner",
                3,
                2,
                "2026-10-19 00:00:00",
                "2026-10-18 00:00:00",
                "2026-10-18 00:00:00",
            ),
        )
        db.execute(
            "INSERT INTO agent_runs(id,tenant_id,ticket_id,state,requester_id,checkpoint,"
            "call_count,input_tokens,output_tokens,cost_microusd,reserved_microusd,budget_microusd,"
            "model,prompt_version,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old-run", "synthetic-tenant", "old-ticket", "completed", "synthetic-owner", "{}",
             1, 100, 50, 200, 0, 1000000, "fixture", "planner-v1", "", "2026-10-18 00:00:00"),
        )
        db.execute(
            "INSERT INTO run_events(tenant_id,run_id,kind,data,created_at) VALUES(?,?,?,?,?)",
            ("synthetic-tenant", "old-run", "model.finished", "{}", "2026-10-18 00:00:00"),
        )
    upgrade("head")
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT name FROM tenants WHERE id='synthetic-tenant'"
            ).fetchone()[0]
            == "合成迁移验收"
        )
        assert "dispatched_at" in {
            row[1] for row in db.execute("PRAGMA table_info(jobs)")
        }
        assert db.execute(
            "SELECT paid_cents,refunded_cents,reserved_cents FROM orders "
            "WHERE id='synthetic-order'"
        ).fetchone() == (10000, 2000, 3000)
        assert db.execute("SELECT COUNT(*) FROM worker_heartbeats").fetchone()[0] == 0
        assert (
            db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "20261020_knowledge"
        )
        assert db.execute("SELECT carrier_code,tracking_number,tracking_phone,logistics_snapshot FROM orders WHERE id='synthetic-order'").fetchone() == ("", "", "", "{}")
        assert "source_ref" in {
            row[1] for row in db.execute("PRAGMA table_info(tickets)")
        }
        assert any(
            row[1] == "uq_ticket_source_ref" and row[2] == 1
            for row in db.execute("PRAGMA index_list(tickets)")
        )
        assert db.execute(
            "SELECT request,state,version,evidence_version,source_ref FROM tickets "
            "WHERE id='old-ticket'"
        ).fetchone() == ("保留原有客户诉求", "waiting_evidence", 3, 2, None)
        assert db.execute("SELECT ticket_id,cost_microusd FROM agent_runs WHERE id='old-run'").fetchone() == ("old-ticket", 200)
        assert db.execute("SELECT run_id FROM run_events").fetchone() == ("old-run",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
