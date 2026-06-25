from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import Settings


class PostgresRepository:
    def __init__(self, settings: Settings) -> None:
        self._database_url = settings.database_url
        self._history_limit = settings.history_limit

    @property
    def configured(self) -> bool:
        return bool(self._database_url)

    def fetch_conversation_history(self, conversation_id: str) -> list[dict[str, Any]]:
        if not self.configured:
            return []

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, metadata, "createdAt"
                FROM "Message"
                WHERE "conversationId" = %s
                ORDER BY "order" DESC
                LIMIT %s;
                """,
                (conversation_id, self._history_limit),
            ).fetchall()

        return [
            {
                "role": row["role"],
                "content": row["content"],
                "metadata": row["metadata"],
                "createdAt": row["createdAt"].isoformat() if row["createdAt"] else None,
            }
            for row in reversed(rows)
        ]

    def aggregate_errors(self, filters: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
        if not self.configured:
            return []

        clauses: list[str] = []
        values: list[Any] = []

        for field, column in [("service", "service"), ("environment", "environment"), ("level", "level")]:
            if filters.get(field):
                values.append(filters[field])
                clauses.append(f"{column} = %s")

        if filters.get("from"):
            values.append(filters["from"])
            clauses.append("last_seen_at >= %s")

        if filters.get("to"):
            values.append(filters["to"])
            clauses.append("first_seen_at <= %s")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  signature,
                  service,
                  environment,
                  level,
                  sample_message,
                  stack_trace,
                  first_seen_at,
                  last_seen_at,
                  occurrence_count
                FROM error_groups
                {where}
                ORDER BY occurrence_count DESC, last_seen_at DESC
                LIMIT %s;
                """,
                tuple(values),
            ).fetchall()

        return [_serialize_row(row) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        connection = psycopg.connect(self._database_url, row_factory=dict_row)
        try:
            yield connection
        finally:
            connection.close()


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }
