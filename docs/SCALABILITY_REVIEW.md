# Scalability & System Design Review

This document is an honest critique of the Event Ledger as submitted: where
it would break under real production load, the math behind those claims,
and the prioritized changes that would fix it. The take-home implementation
intentionally stays simple (in-memory/embedded DBs, single instances, no
queueing) because the brief scoped it to 3-4 hours — this document is the
"if this were going to production" extension of that work.

---

## 1. Current design, summarized

```
Client -> Event Gateway (SQLite, single process) -> Account Service (SQLite, single process)
```

- Two single-instance services, each with an embedded SQLite database.
- Synchronous REST call from Gateway to Account Service on the request path.
- Idempotency via primary-key lookup on `eventId`.
- Balances recomputed as SUM(CREDIT) - SUM(DEBIT) on every read (order-independent
  by construction).
- In-process circuit breaker, in-process metrics counters.

This is correct and defensible for the stated scope. It does not survive
"millions of requests" as-is, and you should be able to say precisely why.

---

## 2. Where it breaks, in order of how soon you'd hit it

### 2.1 SQLite is a single-writer bottleneck
SQLite serializes writes at the file level. There is no horizontal write
scaling, no replication, and no multi-node story. Past a few hundred writes/sec
sustained, write latency degrades sharply as transactions queue for the lock.
This is the first wall you hit, and it's a wall, not a slope.

**Fix:** Postgres (or another real RDBMS), partitioned/sharded by `accountId`.
`accountId` is the natural shard key because every read and write in this
domain is account-scoped — no transaction ever needs to span two accounts in
this design, so sharding introduces no cross-shard transaction problem.

### 2.2 Synchronous blocking call on the critical path
`POST /events` calls Account Service synchronously and blocks the handling
thread for the round trip. FastAPI runs sync `def` handlers in a thread pool
(default ~40 workers via Starlette). Once concurrent in-flight requests
exceed pool size, requests queue regardless of how much CPU or network
capacity is actually free.

**Fix:** `httpx.AsyncClient` + `async def` route handlers so a slow
downstream call doesn't pin a thread. Better still, see 2.3 — don't make the
client wait on the downstream call at all.

### 2.3 A correctness gap, not just a scaling gap
When Account Service is down, `POST /events` returns `503`. The event *is*
stored in the Gateway's DB, but nothing ever retries applying it. There is no
reconciliation job, no background worker, no dead-letter handling. At low
volume, a human notices and replays manually. At millions of events/day, this
silently leaves a population of "received but never applied" transactions
that nobody is looking at - a real money-accuracy bug, not a performance one.

**Fix:** Decouple ingestion from application. Gateway durably enqueues the
event (Kafka, SQS, or similar) and returns `202 Accepted` immediately. A
consumer applies events to Account Service with retry + exponential backoff,
and a dead-letter queue + alert for events that exhaust retries. This is the
single highest-leverage architectural change in this list — it fixes the
correctness gap and the burst-absorption problem at the same time.

### 2.4 Circuit breaker state is per-process
The breaker in `gateway/app/circuit_breaker.py` is an in-memory object. Scale
the Gateway to N replicas behind a load balancer and each replica has its own
breaker — one can be OPEN while N-1 others keep hammering a failing
dependency.

**Fix:** Either move breaker state to Redis (shared OPEN/CLOSED/HALF_OPEN
state across replicas), or explicitly accept per-instance breakers as a
documented tradeoff. The latter is often fine in practice: the breaker is a
fail-fast latency/load-shedding optimization, not a correctness mechanism, so
"inconsistent across replicas" degrades gracefully rather than breaking
anything.

### 2.5 Unbounded list endpoints
`GET /events?account={id}` returns the account's entire history in one
response. A high-volume account (or a long-lived one) eventually returns
megabytes in one call with no way to page through it.

**Fix:** Cursor-based pagination (`?after=<event_timestamp>&limit=100`),
not offset-based (offset pagination degrades on large tables).

### 2.6 Observability that doesn't survive a restart or a second replica
Metrics are in-process dicts: lost on restart, never aggregated across
replicas, no percentile latency, no time series.

**Fix:** Prometheus client library exposing a real `/metrics` scrape
endpoint, scraped centrally; OpenTelemetry SDK exporting spans to
Jaeger/Tempo instead of trace-ID-correlated log lines; SLO-based alerting
(e.g., error budget burn on `POST /events` 5xx rate) instead of "go read the
logs."

### 2.7 No auth, no rate limiting, on a public-facing service
The Gateway is internet-facing per the architecture diagram and currently
accepts unauthenticated requests with no per-client quota. Fine for a
take-home; a same-week gap in production.

**Fix:** API keys or OAuth client-credentials at minimum; per-client rate
limiting (token bucket) at the Gateway edge; request size limits.

### 2.8 No data lifecycle policy
The `events` and `transactions` tables grow forever. No archival, no
retention policy, no separation of hot vs. cold data.

**Fix:** Hot OLTP window (e.g., 90 days) in Postgres, older data archived to
columnar/cold storage (S3 + Parquet, queryable via Athena/BigQuery-style
tooling) for compliance/audit access without bloating the live database.

---

## 3. Capacity planning (back-of-envelope math)

### 3.1 Storage per event

| Component | Fields | Approx. size |
|---|---|---|
| Gateway `events` row | eventId(36) + accountId(20) + type(6) + amount(8) + currency(5) + eventTimestamp(24) + metadata(~100 avg) + flags/receivedAt(~25) + row/index overhead(~80) | **~400 bytes** |
| Account Service `transactions` row | eventId(36) + accountId(20) + type(6) + amount(8) + eventTimestamp(24) + row/index overhead(~50) | **~200 bytes** |
| **Combined per event** | | **~600 bytes** |

This is a simplifying assumption (real overhead depends on the DB engine,
index count, and page fill factor) but it's the right order of magnitude for
a back-of-envelope answer.

### 3.2 Storage at scale

| Daily event volume | Storage/day | Storage/month | Storage/year |
|---|---|---|---|
| 1M | ~600 MB | ~18 GB | ~220 GB |
| 10M | ~6 GB | ~180 GB | ~2.2 TB |
| 100M | ~60 GB | ~1.8 TB | ~22 TB |

### 3.3 Throughput at scale

Assume a 5x peak-to-average burst factor (realistic for batch-style upstream
feeds, which this domain explicitly has - "mainframe-batch" in the sample
payload is a hint).

| Daily event volume | Avg req/s | Peak req/s | SQLite viable? |
|---|---|---|---|
| 1M | ~12 | ~60 | Yes - single instance, single writer is fine |
| 10M | ~116 | ~580 | No - write-lock contention becomes the bottleneck |
| 100M | ~1,160 | ~5,800 | No - needs sharding + async ingestion |

### 3.4 Concurrency (Little's Law)

`concurrent_requests = arrival_rate x service_time`

At 5,800 req/s peak with a 20ms downstream call latency:
`5,800 x 0.020s = 116 concurrent in-flight calls to Account Service`

That's trivial network load, but it's well past what a default sync thread
pool handles cleanly - which is why async I/O (or decoupled queueing) matters
more than raw compute here. CPU is not the bottleneck in this system; thread
pool saturation and single-writer contention are.

---

## 4. Prioritized roadmap (what to say first if asked "what would you do next?")

1. **Decouple ingestion from application via a queue** (Kafka/SQS) - fixes
   the correctness gap (2.3) and the burst-absorption problem in one change.
2. **Move to Postgres, sharded by `accountId`** - removes the single-writer
   ceiling.
3. **Async I/O end-to-end** - removes the thread-pool ceiling.
4. **Shared circuit breaker / rate limiter state (Redis)** once running >1
   Gateway replica.
5. **Real observability stack** (Prometheus + Grafana + OTel/Jaeger) -
   needed before you can safely operate any of the above changes.
6. **Pagination, auth, rate limiting, data lifecycle policy** - all
   necessary, none of them block the others, can be done in parallel.

The ordering matters in an interview: lead with the change that fixes both a
correctness bug and a scale bug at once (#1), not the "biggest" sounding
change. That signals judgment, not just knowledge of buzzwords.
