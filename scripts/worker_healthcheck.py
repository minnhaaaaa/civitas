"""Fast container/Kubernetes probe for the durable worker heartbeat."""

from __future__ import annotations

import os

import psycopg


def check() -> bool:
    database_url = os.getenv("DATABASE_URL", "").replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    worker_id = os.getenv("CIVITAS_WORKER_ID", "").strip()
    try:
        readiness_seconds = int(os.getenv("CIVITAS_WORKER_READINESS_SECONDS", "120"))
    except ValueError:
        return False
    if not database_url or not worker_id or readiness_seconds <= 0:
        return False
    with psycopg.connect(database_url, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM service_heartbeats
                    WHERE service_id = %s
                      AND service_kind = 'worker'
                      AND state = 'running'
                      AND last_seen_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                )
                """,
                (worker_id, readiness_seconds),
            )
            row = cursor.fetchone()
        return bool(row and row[0])


def main() -> int:
    try:
        return 0 if check() else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
