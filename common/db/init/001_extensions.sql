-- Runs once, when the database volume is first created.
-- Tables are created by the code that owns them (ingest creates `chunks`).
CREATE EXTENSION IF NOT EXISTS vector;
