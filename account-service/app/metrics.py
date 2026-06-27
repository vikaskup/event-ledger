from collections import defaultdict
from threading import Lock


class Metrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self.request_counts: dict[str, int] = defaultdict(int)
        self.error_counts: dict[str, int] = defaultdict(int)

    def record_request(self, endpoint: str, is_error: bool) -> None:
        with self._lock:
            self.request_counts[endpoint] += 1
            if is_error:
                self.error_counts[endpoint] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "requestCounts": dict(self.request_counts),
                "errorCounts": dict(self.error_counts),
            }


metrics = Metrics()
