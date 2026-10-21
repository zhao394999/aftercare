import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.importing import parse_csv
from app.main import app
from app.models import Membership, Tenant
from app.security import DomainError
from conftest import ORIGIN, PASSWORD


def test_default_login_skips_disabled_workspaces(world):
    ids, _ = world
    with SessionLocal.begin() as db:
        db.get(Tenant, ids["tenant"]).enabled = False
        db.add(
            Membership(
                tenant_id=ids["other"], user_id=ids["users"]["staff"], role="staff"
            )
        )
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/auth/login",
            json={"email": "staff@test.local", "password": PASSWORD},
            headers={"origin": ORIGIN},
        )
        assert result.status_code == 200, result.text
        assert result.json()["tenant"]["id"] == ids["other"]
        assert client.get("/api/v1/tickets").json()["total"] == 0
        disabled = client.post(
            "/api/v1/auth/login",
            json={"email": "staff@test.local", "password": PASSWORD, "tenant_id": ids["tenant"]},
            headers={"origin": ORIGIN},
        )
        assert disabled.status_code == 403
        assert disabled.json()["code"] == "workspace_disabled"


@pytest.mark.parametrize("oversized_header", [False, True])
def test_csv_field_limit_returns_validation_error(world, oversized_header):
    _, login = world
    large = "x" * 140_000
    header = "external_id,product,quantity,status,placed_at,paid_cents"
    body = f"CSV-LIMIT,{large},1,shipped,2026-10-01T00:00:00Z,100"
    content = (large if oversized_header else header + "\n" + body).encode()
    result = login("staff").post(
        "/api/v1/imports/preview",
        files={"file": ("oversized-field.csv", content, "text/csv")},
    )
    assert result.status_code == 422, result.text
    assert result.json()["code"] == "import_csv"


def test_csv_row_limit_accepts_10000_and_rejects_10001():
    content = "external_id,product,quantity,status,placed_at,paid_cents\n" + "".join(
        f"CSV-{i},商品,1,shipped,2026-10-01T00:00:00Z,100\n" for i in range(10_000)
    )
    rows, errors = parse_csv(content.encode())
    assert len(rows) == 10_000 and errors == []
    with pytest.raises(DomainError) as error:
        parse_csv((content + "OVER-LIMIT,商品,1,shipped,2026-10-01T00:00:00Z,100\n").encode())
    assert error.value.code == "import_rows_limit"
