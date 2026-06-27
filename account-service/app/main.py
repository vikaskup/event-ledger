import time
import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from app.db import get_connection, init_db
from app.models import TransactionRequest, TransactionResponse, BalanceResponse, AccountDetailResponse
from app.logging_config import logger, trace_id_var
from app.metrics import metrics

app = FastAPI(title="Account Service")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.middleware("http")
async def trace_and_metrics_middleware(request: Request, call_next):
    incoming_trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
    token = trace_id_var.set(incoming_trace_id)
    start = time.time()
    try:
        response = await call_next(request)
        is_error = response.status_code >= 400
        metrics.record_request(request.url.path, is_error)
        duration_ms = (time.time() - start) * 1000
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.1f}ms)")
        response.headers["X-Trace-Id"] = incoming_trace_id
        return response
    except Exception:
        metrics.record_request(request.url.path, True)
        logger.exception(f"Unhandled error processing {request.method} {request.url.path}")
        raise
    finally:
        trace_id_var.reset(token)


def _get_or_create_account(conn, account_id: str) -> None:
    conn.execute(
        "INSERT INTO accounts (account_id, balance) VALUES (?, 0) "
        "ON CONFLICT(account_id) DO NOTHING",
        (account_id,),
    )


def _compute_balance(conn, account_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN type = 'CREDIT' THEN amount ELSE -amount END), 0) AS balance "
        "FROM transactions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return row["balance"]


@app.post("/accounts/{account_id}/transactions", response_model=TransactionResponse, status_code=201)
def apply_transaction(account_id: str, txn: TransactionRequest):
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM transactions WHERE event_id = ?", (txn.eventId,)
        ).fetchone()
        if existing:
            logger.info(f"Duplicate transaction event_id={txn.eventId} ignored")
            balance = _compute_balance(conn, account_id)
            return JSONResponse(
                status_code=200,
                content=TransactionResponse(
                    eventId=existing["event_id"],
                    accountId=existing["account_id"],
                    type=existing["type"],
                    amount=existing["amount"],
                    eventTimestamp=existing["event_timestamp"],
                    balanceAfter=balance,
                ).model_dump(),
            )

        _get_or_create_account(conn, account_id)
        conn.execute(
            "INSERT INTO transactions (event_id, account_id, type, amount, event_timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (txn.eventId, account_id, txn.type, txn.amount, txn.eventTimestamp),
        )
        balance = _compute_balance(conn, account_id)
        conn.execute(
            "UPDATE accounts SET balance = ? WHERE account_id = ?", (balance, account_id)
        )
        conn.commit()
        logger.info(f"Applied transaction event_id={txn.eventId} account={account_id} new_balance={balance}")
        return TransactionResponse(
            eventId=txn.eventId,
            accountId=account_id,
            type=txn.type,
            amount=txn.amount,
            eventTimestamp=txn.eventTimestamp,
            balanceAfter=balance,
        )
    finally:
        conn.close()


@app.get("/accounts/{account_id}/balance", response_model=BalanceResponse)
def get_balance(account_id: str):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT account_id FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
        balance = _compute_balance(conn, account_id)
        return BalanceResponse(accountId=account_id, balance=balance)
    finally:
        conn.close()


@app.get("/accounts/{account_id}", response_model=AccountDetailResponse)
def get_account(account_id: str):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT account_id FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
        balance = _compute_balance(conn, account_id)
        txn_rows = conn.execute(
            "SELECT * FROM transactions WHERE account_id = ? ORDER BY event_timestamp ASC",
            (account_id,),
        ).fetchall()
        transactions = [
            TransactionResponse(
                eventId=t["event_id"],
                accountId=t["account_id"],
                type=t["type"],
                amount=t["amount"],
                eventTimestamp=t["event_timestamp"],
                balanceAfter=balance,
            )
            for t in txn_rows
        ]
        return AccountDetailResponse(accountId=account_id, balance=balance, transactions=transactions)
    finally:
        conn.close()


@app.get("/health")
def health():
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return {"status": "ok" if db_status == "ok" else "degraded", "service": "account-service", "database": db_status}


@app.get("/metrics")
def get_metrics():
    return metrics.snapshot()
