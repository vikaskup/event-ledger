import time
from enum import Enum
from threading import Lock


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    """Hand-rolled circuit breaker.

    CLOSED: calls pass through. failure_count tracked.
    Once failure_count >= failure_threshold -> OPEN.
    OPEN: calls fail fast for recovery_timeout seconds.
    After timeout elapses -> HALF_OPEN: next call is allowed through as a probe.
    Probe success -> CLOSED (reset). Probe failure -> OPEN again, timer restarts.
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 10.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._lock = Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.time() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN

    def before_call(self) -> None:
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError("Circuit breaker is OPEN: Account Service calls suspended")

    def on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
