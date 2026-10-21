import os

import pytest
from app import domain
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import Membership, User, now, uid
from app.security import DomainError, hasher
from app.storage import s3_client
from fastapi.testclient import TestClient
from test_postgres import pgworld as pgworld

pytestmark = pytest.mark.skipif(
    os.environ.get("STORAGE_BACKEND") != "s3",
    reason="Requires real private S3 acceptance bucket",
)


def fields():
    return {
        "kind": "logistics",
        "title": "合成私有附件",
        "source": "独立存储验收",
        "observed_at": now().isoformat() + "Z",
    }


def test_real_s3_private_read_and_cross_tenant_denied(pgworld):
    ids, login = pgworld
    staff = login("staff")
    content = b"synthetic private S3 attachment"
    result = staff.post(
        f"/api/v1/tickets/{ids['tickets'][0][0]}/evidence",
        data=fields(),
        files={"file": ("private.txt", content, "text/plain")},
    )
    assert result.status_code == 201, result.text
    evidence = result.json()
    assert staff.get("/api/v1/evidence/" + evidence["id"] + "/file").content == content
    email = uid() + "@synthetic.invalid"
    password = "Synthetic-storage-test-password-12"
    with SessionLocal.begin() as db:
        user = User(
            email=email, name="合成其他租户", password_hash=hasher.hash(password)
        )
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=ids["other"], user_id=user.id, role="staff"))
    with TestClient(app) as outsider:
        response = outsider.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"origin": "http://127.0.0.1:5173"},
        )
        assert response.status_code == 200
        assert (
            outsider.get("/api/v1/evidence/" + evidence["id"] + "/file").status_code
            == 404
        )
    s3_client().delete_object(Bucket=settings().s3_bucket, Key=evidence["file_key"])


def test_real_s3_rollback_removes_uploaded_object(pgworld, monkeypatch):
    ids, login = pgworld
    staff = login("staff")

    def reject(*args, **kwargs):
        raise DomainError("synthetic_conflict", "合成事务失败")

    monkeypatch.setattr(domain, "audit", reject)
    response = staff.post(
        f"/api/v1/tickets/{ids['tickets'][0][0]}/evidence",
        data=fields(),
        files={"file": ("rollback.txt", b"synthetic rollback", "text/plain")},
    )
    assert response.status_code == 409
    objects = s3_client().list_objects_v2(
        Bucket=settings().s3_bucket, Prefix=ids["tenant"] + "/"
    )
    assert objects.get("KeyCount", 0) == 0
