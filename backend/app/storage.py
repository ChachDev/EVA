import json
from pathlib import Path
import sqlite3


class EvaluationStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)

    def create(self, record: dict) -> None:
        payload = json.dumps(record, ensure_ascii=False, allow_nan=False)
        with sqlite3.connect(self.path, timeout=5) as connection:
            connection.execute(
                "INSERT INTO evaluations(id, created_at, payload) VALUES (?, ?, ?)",
                (record["id"], record["created_at"], payload),
            )

    def get(self, evaluation_id: str) -> dict | None:
        with sqlite3.connect(self.path, timeout=5) as connection:
            row = connection.execute("SELECT payload FROM evaluations WHERE id = ?", (evaluation_id,)).fetchone()
        return json.loads(row[0]) if row else None

