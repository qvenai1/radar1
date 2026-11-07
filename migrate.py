#!/usr/bin/env python3
"""
Migration runner — plain SQL files, applied in lexical order.
==============================================================
Tool choice (fixed for this project): versioned SQL files in migrations/
executed by this runner, tracked in a schema_migrations table. No ORM, no
Alembic — the schema is SQL-first (hypertables, continuous aggregates, CHECK
constraints), so migrations are written in the language that defines them.

Rules
-----
* Files run in lexical order: 001_..., 002_..., ...
* A file runs exactly once. Its sha256 is recorded; if an applied file is
  later edited, the runner REFUSES to continue (schema drift is an error,
  never silently accepted). Write a new migration instead.
* Each file runs inside a transaction, UNLESS it contains the directive
  `-- migrate:no-transaction` (needed for continuous aggregates, which
  PostgreSQL forbids inside transactions). Such files are split into
  individual statements (dollar-quote and string-literal aware) and executed
  one at a time.

Usage
    python migrate.py             # apply all pending
    python migrate.py --status    # show applied/pending, change nothing
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
NO_TX_DIRECTIVE = "-- migrate:no-transaction"

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _split_statements(sql: str) -> list[str]:
    """
    Split a SQL script into statements on ';', respecting single-quoted
    strings, dollar-quoted bodies, and line comments. Sufficient for the
    plain DDL/SELECT statements allowed in no-transaction migrations.
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    in_squote = False
    dollar_tag: str | None = None

    while i < n:
        ch = sql[i]
        if dollar_tag:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
        elif in_squote:
            if ch == "'":
                in_squote = False
        elif ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            buf.append(sql[i:end])
            i = end
            continue
        elif ch == "'":
            in_squote = True
        elif ch == "$":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                dollar_tag = sql[i : j + 1]
                buf.append(dollar_tag)
                i = j + 1
                continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


async def run(status_only: bool) -> int:
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"No migrations found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(SCHEMA_MIGRATIONS_DDL)
        applied = {
            r["filename"]: r["checksum"]
            for r in await conn.fetch("SELECT filename, checksum FROM schema_migrations")
        }

        pending: list[Path] = []
        for path in files:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    print(
                        f"ERROR: {path.name} was modified after being applied "
                        f"(checksum mismatch). Write a new migration instead.",
                        file=sys.stderr,
                    )
                    return 1
                print(f"  applied  {path.name}")
            else:
                pending.append(path)

        if status_only:
            for path in pending:
                print(f"  PENDING  {path.name}")
            return 0

        for path in pending:
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            no_tx = NO_TX_DIRECTIVE in sql
            print(f"  applying {path.name} ({'no-transaction' if no_tx else 'transactional'})")
            if no_tx:
                for stmt in _split_statements(sql):
                    await conn.execute(stmt)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)",
                    path.name, checksum,
                )
            else:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)",
                        path.name, checksum,
                    )
            print(f"  done     {path.name}")

        print("All migrations applied.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Apply SQL migrations in order.")
    p.add_argument("--status", action="store_true", help="Show status without applying")
    a = p.parse_args()
    return asyncio.run(run(a.status))


if __name__ == "__main__":
    sys.exit(main())
