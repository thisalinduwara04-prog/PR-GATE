import pytest


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000"])
def test_api_allowed_for_localhost(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["abc.ngrok-free.app", "evil.com", "localhost.evil.com", ""])
def test_api_forbidden_for_other_hosts(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 403


def test_cors_allows_dashboard_origin(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_rejects_other_origin(client):
    r = client.get("/api/health", headers={"Origin": "http://evil.com"})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_cors_preflight_rejects_other_write_methods(client, method):
    r = client.options("/api/health", headers={"Origin": "http://localhost:5173",
                                               "Access-Control-Request-Method": method})
    assert r.status_code == 400
