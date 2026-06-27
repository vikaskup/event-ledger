"""Full integration test: a real Account Service process + the Gateway,
exercising the actual Gateway -> Account Service HTTP call (no mocking).
"""
import os
import sys
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn

ACCOUNT_SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "account-service"))
GATEWAY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _purge_app_modules():
    # Both services use a top-level package literally named "app" - drop any
    # cached copies before switching sys.path so the right one gets imported.
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(scope="module")
def real_account_service():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["ACCOUNT_DB_PATH"] = db_path

    sys.path.remove(GATEWAY_DIR) if GATEWAY_DIR in sys.path else None
    sys.path.insert(0, ACCOUNT_SERVICE_DIR)
    _purge_app_modules()

    from importlib import import_module
    account_main = import_module("app.main")

    config = uvicorn.Config(account_main.app, host="127.0.0.1", port=8011, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(50):
        try:
            httpx.get("http://127.0.0.1:8011/health", timeout=0.5)
            break
        except httpx.RequestError:
            time.sleep(0.1)

    yield "http://127.0.0.1:8011"

    server.should_exit = True
    thread.join(timeout=5)
    os.remove(db_path)
    sys.path.remove(ACCOUNT_SERVICE_DIR)
    _purge_app_modules()


@pytest.fixture()
def gateway_client(real_account_service):
    os.environ["ACCOUNT_SERVICE_URL"] = real_account_service
    fd, gw_db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["GATEWAY_DB_PATH"] = gw_db_path

    sys.path.insert(0, GATEWAY_DIR)
    _purge_app_modules()

    from importlib import import_module
    gw_main = import_module("app.main")

    from fastapi.testclient import TestClient
    with TestClient(gw_main.app) as c:
        yield c

    os.remove(gw_db_path)
    _purge_app_modules()


def test_full_flow_gateway_to_real_account_service(gateway_client):
    resp = gateway_client.post(
        "/events",
        json={
            "eventId": "evt-integration-1",
            "accountId": "acct-integration",
            "type": "CREDIT",
            "amount": 75.0,
            "currency": "USD",
            "eventTimestamp": "2026-01-01T12:00:00Z",
        },
    )
    assert resp.status_code == 201

    balance_resp = gateway_client.get("/accounts/acct-integration/balance")
    assert balance_resp.status_code == 200
    assert balance_resp.json()["balance"] == 75.0

    event_resp = gateway_client.get("/events/evt-integration-1")
    assert event_resp.status_code == 200
    assert event_resp.json()["accountId"] == "acct-integration"
