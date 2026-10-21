import httpx
import logging
import pytest

from app import agent, storage
from app.worker import run_once
from test_agent_runtime import start


@pytest.mark.parametrize("style", ["path", "virtual"])
def test_custom_s3_endpoint_uses_configured_url_style(configured, monkeypatch, style):
    monkeypatch.setattr(configured, "s3_endpoint", "https://storage.example.test")
    monkeypatch.setattr(configured, "s3_addressing_style", style)
    options = {}

    def client(service, **kwargs):
        assert service == "s3"
        options.update(kwargs)

    monkeypatch.setattr(storage.boto3, "client", client)
    storage.s3_client()
    assert options["config"].s3["addressing_style"] == style


def test_model_key_stays_out_of_api_responses_and_failure_logs(
    world, configured, monkeypatch, caplog
):
    ids, login = world
    caplog.set_level(logging.INFO)
    admin, staff = login("admin"), login("staff")
    marker = "server-private-credential-for-leak-check"
    monkeypatch.setattr(configured, "llm_api_key", marker)
    original = httpx.Client

    def reject(request):
        assert request.headers["Authorization"] == "Bearer " + marker
        return httpx.Response(401, json={"error": "Rejected " + marker})

    monkeypatch.setattr(
        agent.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(reject), **kwargs),
    )
    run_id = start(staff, ids)
    run_once()
    for path in [
        "/system", "/auth/me", "/settings", "/overview", "/runs", "/runs/" + run_id
    ]:
        response = admin.get("/api/v1" + path)
        assert response.status_code == 200, response.text
        assert marker not in response.text
    assert marker not in caplog.text
