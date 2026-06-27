import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "gateway.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            metadata TEXT,
            applied_to_account INTEGER NOT NULL DEFAULT 0,
            received_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_id)")
    conn.commit()
    conn.close()
