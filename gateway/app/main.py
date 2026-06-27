import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from app.db import get_connection, init_db
from app.models import EventRequest, EventResponse
from app.logging_config import logger, trace_id_var
from app.metrics import metrics
from app.account_client import apply_transaction, get_balance, AccountServiceUnavailableError, breaker

app = FastAPI(title="Event Gateway")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.middleware("http")
async def trace_and_metrics_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
    token = trace_id_var.set(trace_id)
    start = time.time()
    try:
        response = await call_next(request)
        is_error = response.status_code >= 400
        metrics.record_request(request.url.path, is_error)
        duration_ms = (time.time() - start) * 1000
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.1f}ms)")
        response.headers["X-Trace-Id"] = trace_id
        return response
    except Exception:
        metrics.record_request(request.url.path, True)
        logger.exception(f"Unhandled error processing {request.method} {request.url.path}")
        raise
    finally:
        trace_id_var.reset(token)


def _row_to_event_response(row) -> EventResponse:
    return EventResponse(
        eventId=row["event_id"],
        accountId=row["account_id"],
        type=row["type"],
        amount=row["amount"],
        currency=row["currency"],
        eventTimestamp=row["event_timestamp"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
    )


@app.post("/events", status_code=201)
def submit_event(event: EventRequest, request: Request):
    trace_id = trace_id_var.get()
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event.eventId,)
        ).fetchone()
        if existing:
            logger.info(f"Duplicate event_id={event.eventId} received, returning original")
            return JSONResponse(status_code=200, content=_row_to_event_response(existing).model_dump())

        conn.execute(
            "INSERT INTO events (event_id, account_id, type, amount, currency, event_timestamp, "
            "metadata, applied_to_account, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                event.eventId,
                event.accountId,
                event.type,
                event.amount,
                event.currency,
                event.eventTimestamp,
                json.dumps(event.metadata) if event.metadata else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        logger.info(f"Stored event_id={event.eventId} account={event.accountId}, calling Account Service")

        try:
            apply_transaction(
                event.accountId,
                {
                    "eventId": event.eventId,
                    "type": event.type,
                    "amount": event.amount,
                    "eventTimestamp": event.eventTimestamp,
                },
                trace_id,
            )
            conn.execute(
                "UPDATE events SET applied_to_account = 1 WHERE event_id = ?", (event.eventId,)
            )
            conn.commit()
        except AccountServiceUnavailableError as exc:
            logger.warning(f"Account Service unavailable while applying event_id={event.eventId}: {exc}")
            raise HTTPException(
                status_code=503,
                detail="Event recorded, but Account Service is currently unavailable. "
                       "The transaction has not yet been applied to the account balance.",
            )

        row = conn.execute("SELECT * FROM events WHERE event_id = ?", (event.eventId,)).fetchone()
        return _row_to_event_response(row)
    finally:
        conn.close()


@app.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
        return _row_to_event_response(row)
    finally:
        conn.close()


@app.get("/events")
def list_events(account: str):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE account_id = ? ORDER BY event_timestamp ASC",
            (account,),
        ).fetchall()
        return [_row_to_event_response(r).model_dump() for r in rows]
    finally:
        conn.close()


@app.get("/accounts/{account_id}/balance")
def proxy_balance(account_id: str):
    trace_id = trace_id_var.get()
    try:
        result = get_balance(account_id, trace_id)
    except AccountServiceUnavailableError:
        raise HTTPException(status_code=503, detail="Account Service is currently unreachable")
    if result is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    return result


@app.get("/health")
def health():
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": "gateway",
        "database": db_status,
        "circuitBreakerState": breaker.state.value,
    }


@app.get("/metrics")
def get_metrics():
    return metrics.snapshot()
