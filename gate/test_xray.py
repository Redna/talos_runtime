import json
import pytest
from pathlib import Path
from datetime import datetime, timezone


def test_xray_history_list():
    from starlette.testclient import TestClient
    from app import app

    client = TestClient(app)
    response = client.get("/v1/xray/history?count=5")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_xray_history_detail_not_found():
    from starlette.testclient import TestClient
    from app import app

    client = TestClient(app)
    response = client.get("/v1/xray/history/nonexistent.json")
    assert response.status_code == 404


def test_sse_endpoints_removed():
    from starlette.testclient import TestClient
    from app import app

    client = TestClient(app)
    for endpoint in ["/v1/xray/stream", "/v1/xray/state", "/v1/xray/events"]:
        response = client.get(endpoint, follow_redirects=False)
        assert response.status_code in (404, 405), (
            f"Endpoint {endpoint} should be removed"
        )
