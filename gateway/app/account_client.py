import os
import httpx

from app.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.logging_config import logger

ACCOUNT_SERVICE_URL = os.environ.get("ACCOUNT_SERVICE_URL", "http://localhost:8001")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("ACCOUNT_SERVICE_TIMEOUT", "3.0"))

breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)


class AccountServiceUnavailableError(Exception):
    pass


def apply_transaction(account_id: str, payload: dict, trace_id: str) -> dict:
    try:
        breaker.before_call()
    except CircuitOpenError as exc:
        logger.warning(f"Circuit breaker OPEN - skipping call to Account Service for account={account_id}")
        raise AccountServiceUnavailableError(str(exc)) from exc

    try:
        response = httpx.post(
            f"{ACCOUNT_SERVICE_URL}/accounts/{account_id}/transactions",
            json=payload,
            headers={"X-Trace-Id": trace_id},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 500:
            breaker.on_failure()
            raise AccountServiceUnavailableError(f"Account Service returned {response.status_code}")
        breaker.on_success()
        return response.json()
    except httpx.RequestError as exc:
        breaker.on_failure()
        logger.error(f"Account Service call failed: {exc}")
        raise AccountServiceUnavailableError(str(exc)) from exc


def get_balance(account_id: str, trace_id: str) -> dict:
    try:
        breaker.before_call()
    except CircuitOpenError as exc:
        raise AccountServiceUnavailableError(str(exc)) from exc

    try:
        response = httpx.get(
            f"{ACCOUNT_SERVICE_URL}/accounts/{account_id}/balance",
            headers={"X-Trace-Id": trace_id},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 500:
            breaker.on_failure()
            raise AccountServiceUnavailableError(f"Account Service returned {response.status_code}")
        breaker.on_success()
        if response.status_code == 404:
            return None
        return response.json()
    except httpx.RequestError as exc:
        breaker.on_failure()
        logger.error(f"Account Service call failed: {exc}")
        raise AccountServiceUnavailableError(str(exc)) from exc
