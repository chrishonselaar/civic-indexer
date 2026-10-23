"""Postgres access: connection, migration runner, upsert helpers.

Upsert idiom ported from kapvergunningen-inventarisatie/db.py (ON CONFLICT DO UPDATE),
translated from SQLite to psycopg3/Postgres.
"""

import json
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from engine.config import DATABASE_URL, MIGRATIONS_DIR


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@contextmanager
def tx() -> Iterator[psycopg.Cursor]:
    """One transaction: commits on success, rolls back on exception."""
    with connect() as conn:
        with conn.cursor() as cur:
            yield cur


def jsonb(value: Any) -> Json:
    return Json(value, dumps=lambda v: json.dumps(v, ensure_ascii=False, default=str))


def migrate() -> list[str]:
    """Apply pending migrations/NNNN_*.sql in filename order. Returns applied filenames."""
    applied: list[str] = []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('schema_migrations') IS NOT NULL AS present"
            )
            have_table = cur.fetchone()["present"]
            done: set[str] = set()
            if have_table:
                cur.execute("SELECT filename FROM schema_migrations")
                done = {r["filename"] for r in cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("[0-9]*.sql")):
            if path.name in done:
                continue
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s) "
                    "ON CONFLICT (filename) DO NOTHING",
                    (path.name,),
                )
            conn.commit()
            applied.append(path.name)
    return applied


def upsert(
    cur: psycopg.Cursor,
    table: str,
    row: dict[str, Any],
    conflict: str,
    skip_update: tuple[str, ...] = (),
) -> None:
    """INSERT ... ON CONFLICT (conflict) DO UPDATE for every non-conflict column.

    Columns in skip_update keep their existing value on conflict
    (e.g. discovered_at, first_seen).
    """
    cols = list(row.keys())
    conflict_cols = {c.strip() for c in conflict.split(",")}
    updates = [
        c for c in cols if c not in conflict_cols and c not in skip_update
    ]
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT ({conflict}) "
    )
    if updates:
        sql += "DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in updates)
    else:
        sql += "DO NOTHING"
    cur.execute(sql, tuple(row.values()))
