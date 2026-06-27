# Event Ledger

A two-service system that ingests financial transaction events and maintains
account balances, designed to tolerate duplicate and out-of-order delivery
from upstream systems, with tracing, observability, and resiliency built in.

## Architecture

```
Client ──REST──▶ Event Gateway (public, :8000/:8080)
                     │  - validates & stores events (its own SQLite DB)
                     │  - enforces idempotency on eventId
                     │  - generates/propagates trace ID
                     │  - circuit-breaks calls to Account Service
                     │
                     │ REST (sync, X-Trace-Id header)
                     ▼
              Account Service (internal, :8001)
                     - owns balances & transaction history (its own SQLite DB)
                     - balance = sum(CREDIT) - sum(DEBIT), recomputed from
                       all transactions, so arrival order never matters
                     - idempotent on eventId at this layer too
```

**Event Gateway** (`gateway/`) is the only service clients talk to. It stores
every submitted event locally (so reads never depend on the Account Service),
then forwards the transaction to the Account Service synchronously.

**Account Service** (`account-service/`) is the source of truth for account
balances. It is never called directly by clients — only by the Gateway.

The two services do not share a database or process. They communicate only
over HTTP, with the Gateway acting as the resilient client.

### Why this design handles the two hard requirements

- **Duplicates**: both services key on `eventId`. A duplicate `POST /events`
  (or duplicate downstream `POST /accounts/{id}/transactions`) is detected by
  a primary-key lookup before insert; the original record is returned instead
  of applying the transaction again.
- **Out-of-order**: nothing in the system depends on arrival order. Balances
  are computed as `SUM(CREDIT) - SUM(DEBIT)` over the full set of transactions
  every time, not as a running total applied sequentially. Listings sort by
  `eventTimestamp` at read time, not insertion time.

## API

### Event Gateway (public)
| Method | Endpoint | Notes |
|---|---|---|
| POST | `/events` | Submit an event. `201` on first success, `200` + original event on duplicate, `503` if Account Service is unreachable, `422` on validation failure. |
| GET | `/events/{id}` | Local read only — works even if Account Service is down. |
| GET | `/events?account={id}` | Local read, sorted by `eventTimestamp`. |
| GET | `/accounts/{id}/balance` | Proxies to Account Service; `503` if unreachable. |
| GET | `/health` | Includes circuit breaker state. |
| GET | `/metrics` | Request/error counts per endpoint. |

### Account Service (internal)
| Method | Endpoint | Notes |
|---|---|---|
| POST | `/accounts/{id}/transactions` | Idempotent on `eventId`. |
| GET | `/accounts/{id}/balance` | |
| GET | `/accounts/{id}` | Account + transaction history, sorted by `eventTimestamp`. |
| GET | `/health` | |
| GET | `/metrics` | |

## Distributed tracing

The Gateway generates (or accepts, via `X-Trace-Id` on the inbound request) a
trace ID per request, holds it in a `contextvar`, passes it to the Account
Service as the `X-Trace-Id` header, and both services emit structured JSON
logs that include it — so a single client request can be grepped end-to-end
across both services' logs by trace ID.

## Resiliency: circuit breaker

The Gateway wraps its HTTP call to the Account Service in a hand-rolled
circuit breaker (`gateway/app/circuit_breaker.py`):

- **CLOSED** — calls pass through normally.
- After **3** consecutive failures (connection errors or 5xx) → **OPEN**:
  calls fail fast (no network attempt) and the Gateway returns `503`
  immediately instead of hanging on a dead dependency.
- After a **10s** recovery timeout, the next call is allowed through as a
  probe (**HALF_OPEN**). Success closes the circuit; failure re-opens it.

**Why a circuit breaker over retry/backoff or a bulkhead:** the Account
Service call is on the synchronous critical path of `POST /events`. If it's
down, retrying just adds latency to every request without changing the
outcome, and a bulkhead alone doesn't stop us from hammering a dead service.
A circuit breaker gives a fast, predictable failure mode for callers and lets
the dependency recover without being immediately re-flooded once it's back.

Current breaker state is visible at `GET /health` (`circuitBreakerState`).

## Graceful degradation

When the Account Service is unavailable:
- `POST /events` → `503` (event is still stored locally; only the downstream
  balance update is blocked).
- `GET /events/{id}`, `GET /events?account=` → unaffected, since they only
  read the Gateway's own database.
- `GET /accounts/{id}/balance` → `503` with a clear message.

## Running locally

### Option A: Docker Compose (preferred)

```bash
docker-compose up --build
```

- Gateway: `http://localhost:8080` (mapped from the container's internal
  port 8000 — remapped from the default 8000 to avoid local port conflicts;
  edit `docker-compose.yml` if you'd rather use 8000)
- Account Service: `http://localhost:8001`

```bash
docker-compose down -v   # stop and remove volumes
```

### Option B: Run manually

Each service needs its own virtualenv.

```bash
# Account Service
cd account-service
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8001

# Gateway (separate terminal)
cd gateway
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ACCOUNT_SERVICE_URL=http://localhost:8001 .venv/bin/uvicorn app.main:app --port 8000
```

## Running tests

```bash
# Account Service
cd account-service && .venv/bin/python -m pytest tests/ -v

# Gateway (includes a full integration test that boots a real Account
# Service process and exercises the actual HTTP call, no mocking)
cd gateway && .venv/bin/python -m pytest tests/ -v
```

Test coverage includes: idempotency (both services), out-of-order listing
and balance correctness, validation errors, circuit breaker opening under
repeated downstream failure, trace ID propagation/generation, and one
end-to-end integration test against a real Account Service.

## Example request

```bash
curl -X POST http://localhost:8080/events \
  -H "Content-Type: application/json" \
  -d '{
    "eventId": "evt-001",
    "accountId": "acct-123",
    "type": "CREDIT",
    "amount": 150.00,
    "currency": "USD",
    "eventTimestamp": "2026-05-15T14:02:11Z",
    "metadata": {"source": "mainframe-batch", "batchId": "B-9042"}
  }'
```

## Design notes / things I'd do next with more time

- Swap the in-process circuit breaker state for something shared (e.g. Redis)
  if the Gateway ever runs as multiple replicas — right now it's per-process.
- Async fallback queue (mentioned as a bonus) so writes aren't lost-in-spirit
  during an outage, just delayed in applying to the balance.
- OpenTelemetry SDK + Jaeger for real trace visualization instead of
  log-correlated trace IDs.
- Prometheus-formatted `/metrics` instead of the simple JSON counters.
