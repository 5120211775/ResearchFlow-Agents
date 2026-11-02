"""Dialect-adaptive column types."""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Uuid
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# Shared instances are safe: TypeEngine objects are immutable after construction and
# SQLAlchemy copies them per-column as needed.
JsonType = JSON().with_variant(JSONB(), "postgresql")
UuidType = Uuid(as_uuid=True).with_variant(PG_UUID(as_uuid=True), "postgresql")

# Autoincrement big-int PKs. On Postgres this is plain BIGINT + SERIAL, exactly as
# before. On SQLite, BIGINT is *not* a rowid alias, so an insert without an explicit
# id fails `NOT NULL` — only `INTEGER PRIMARY KEY` auto-populates. This variant is
# what makes agent_logs/audit_log inserts work on the desktop.
BigIntAutoType = BigInteger().with_variant(sqlite.INTEGER(), "sqlite")
