def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_apply_transaction_creates_account_and_balance(client):
    resp = client.post(
        "/accounts/acct-1/transactions",
        json={"eventId": "evt-1", "type": "CREDIT", "amount": 100, "eventTimestamp": "2026-01-01T00:00:00Z"},
    )
    assert resp.status_code == 201
    assert resp.json()["balanceAfter"] == 100.0

    balance = client.get("/accounts/acct-1/balance")
    assert balance.status_code == 200
    assert balance.json()["balance"] == 100.0


def test_idempotent_duplicate_transaction_does_not_double_apply(client):
    payload = {"eventId": "evt-dup", "type": "CREDIT", "amount": 50, "eventTimestamp": "2026-01-01T00:00:00Z"}
    first = client.post("/accounts/acct-2/transactions", json=payload)
    assert first.status_code == 201

    second = client.post("/accounts/acct-2/transactions", json=payload)
    assert second.status_code == 200

    balance = client.get("/accounts/acct-2/balance")
    assert balance.json()["balance"] == 50.0


def test_balance_is_sum_of_credits_minus_debits_regardless_of_order(client):
    client.post(
        "/accounts/acct-3/transactions",
        json={"eventId": "evt-a", "type": "CREDIT", "amount": 200, "eventTimestamp": "2026-01-02T00:00:00Z"},
    )
    client.post(
        "/accounts/acct-3/transactions",
        json={"eventId": "evt-b", "type": "DEBIT", "amount": 30, "eventTimestamp": "2026-01-01T00:00:00Z"},
    )
    balance = client.get("/accounts/acct-3/balance")
    assert balance.json()["balance"] == 170.0


def test_validation_rejects_zero_or_negative_amount(client):
    resp = client.post(
        "/accounts/acct-4/transactions",
        json={"eventId": "evt-bad", "type": "CREDIT", "amount": 0, "eventTimestamp": "2026-01-01T00:00:00Z"},
    )
    assert resp.status_code == 422


def test_validation_rejects_unknown_type(client):
    resp = client.post(
        "/accounts/acct-5/transactions",
        json={"eventId": "evt-bad2", "type": "WIRE", "amount": 10, "eventTimestamp": "2026-01-01T00:00:00Z"},
    )
    assert resp.status_code == 422


def test_balance_for_unknown_account_is_404(client):
    resp = client.get("/accounts/unknown-acct/balance")
    assert resp.status_code == 404


def test_metrics_endpoint_tracks_requests(client):
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.json()["requestCounts"]["/health"] >= 1
