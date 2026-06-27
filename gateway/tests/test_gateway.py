import respx
import httpx
import app.account_client as account_client


def _event_payload(event_id="evt-1", account_id="acct-1", amount=100, ts="2026-01-01T10:00:00Z", type_="CREDIT"):
    return {
        "eventId": event_id,
        "accountId": account_id,
        "type": type_,
        "amount": amount,
        "currency": "USD",
        "eventTimestamp": ts,
    }


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["circuitBreakerState"] == "CLOSED"


def test_validation_rejects_negative_amount(client):
    payload = _event_payload(amount=-5)
    resp = client.post("/events", json=payload)
    assert resp.status_code == 422


def test_validation_rejects_unknown_type(client):
    payload = _event_payload(type_="WIRE")
    resp = client.post("/events", json=payload)
    assert resp.status_code == 422


@respx.mock
def test_submit_event_happy_path_calls_account_service(client):
    route = respx.post(f"{account_client.ACCOUNT_SERVICE_URL}/accounts/acct-1/transactions").mock(
        return_value=httpx.Response(201, json={
            "eventId": "evt-1", "accountId": "acct-1", "type": "CREDIT",
            "amount": 100, "eventTimestamp": "2026-01-01T10:00:00Z", "balanceAfter": 100.0,
        })
    )
    resp = client.post("/events", json=_event_payload())
    assert resp.status_code == 201
    assert route.called

    fetched = client.get("/events/evt-1")
    assert fetched.status_code == 200
    assert fetched.json()["accountId"] == "acct-1"


@respx.mock
def test_duplicate_event_returns_original_without_recalling_downstream(client):
    route = respx.post(f"{account_client.ACCOUNT_SERVICE_URL}/accounts/acct-1/transactions").mock(
        return_value=httpx.Response(201, json={
            "eventId": "evt-1", "accountId": "acct-1", "type": "CREDIT",
            "amount": 100, "eventTimestamp": "2026-01-01T10:00:00Z", "balanceAfter": 100.0,
        })
    )
    client.post("/events", json=_event_payload())
    resp = client.post("/events", json=_event_payload())
    assert resp.status_code == 200
    assert route.call_count == 1


@respx.mock
def test_events_listed_in_chronological_order_regardless_of_arrival(client):
    respx.post(f"{account_client.ACCOUNT_SERVICE_URL}/accounts/acct-2/transactions").mock(
        return_value=httpx.Response(201, json={"balanceAfter": 0})
    )
    client.post("/events", json=_event_payload(event_id="evt-late", account_id="acct-2", ts="2026-01-02T00:00:00Z"))
    client.post("/events", json=_event_payload(event_id="evt-early", account_id="acct-2", ts="2026-01-01T00:00:00Z"))

    listed = client.get("/events?account=acct-2")
    ids = [e["eventId"] for e in listed.json()]
    assert ids == ["evt-early", "evt-late"]


@respx.mock
def test_account_service_down_returns_503_and_local_reads_still_work(client):
    respx.post(f"{account_client.ACCOUNT_SERVICE_URL}/accounts/acct-3/transactions").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    resp = client.post("/events", json=_event_payload(event_id="evt-3", account_id="acct-3"))
    assert resp.status_code == 503

    fetched = client.get("/events/evt-3")
    assert fetched.status_code == 200


@respx.mock
def test_circuit_breaker_opens_after_repeated_failures(client):
    respx.post(f"{account_client.ACCOUNT_SERVICE_URL}/accounts/acct-4/transactions").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    for i in range(account_client.breaker.failure_threshold):
        client.post("/events", json=_event_payload(event_id=f"evt-cb-{i}", account_id="acct-4"))

    assert account_client.breaker.state.value == "OPEN"

    health = client.get("/health")
    assert health.json()["circuitBreakerState"] == "OPEN"


def test_trace_id_propagated_and_returned_in_response_header(client):
    resp = client.get("/health", headers={"X-Trace-Id": "trace-abc-123"})
    assert resp.headers["X-Trace-Id"] == "trace-abc-123"


def test_trace_id_generated_when_not_provided(client):
    resp = client.get("/health")
    assert "X-Trace-Id" in resp.headers
    assert len(resp.headers["X-Trace-Id"]) > 0
