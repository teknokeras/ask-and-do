import os
import uuid

import psycopg
import pytest

from common.settings import get_settings

# The columns search reads, as prepare/ingest/store.py creates them. Copied rather
# than imported because runtime never imports prepare: the table is the handoff.
CHUNKS_TABLE = """
    CREATE TABLE chunks (
      id         text PRIMARY KEY,
      url        text NOT NULL,
      anchor     text,
      title      text,
      breadcrumb text NOT NULL,
      body       text NOT NULL,
      embedding  vector(3) NOT NULL,
      tsv        tsvector GENERATED ALWAYS AS
                   (to_tsvector('english', breadcrumb || ' ' || body)) STORED
    )
"""


@pytest.fixture
def db():
    """A throwaway schema in the dev database (just db-up). Skips when no database
    is running, except in CI, where a skip would hide untested search code."""
    try:
        conn = psycopg.connect(get_settings().database_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("CI"):
            raise
        pytest.skip("database not running (just db-up)")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(f"CREATE SCHEMA {schema}")
    # public stays on the path because the vector extension lives there.
    conn.execute(f"SET search_path TO {schema}, public")
    conn.execute(CHUNKS_TABLE)
    try:
        yield conn
    finally:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()
