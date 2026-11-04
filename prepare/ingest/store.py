"""Writing chunks to Postgres: the chunks table, embeddings in a pgvector
column, and a generated full-text column for hybrid search.

Re-runs are incremental, and embedding is the slow part, so the store only
embeds what changed:
- a page whose page_hash matches what is stored is skipped entirely;
- a chunk whose chunk_hash (breadcrumb + body) is unchanged keeps its vector
  and only gets its metadata (anchor, title, position) updated;
- new or edited chunks are embedded and upserted by their stable ID;
- chunks that are no longer on a page are deleted.
Each page is written in its own transaction, so an interrupted run keeps the
pages it finished and the next run picks up the rest.

The embedding model and dimension are recorded in ingest_meta. Vectors from
two models are not comparable, so a run with a different model is refused
until the table is rebuilt with reset=True.
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from common.embedder import Embedder
from prepare.ingest.chunk import Chunk


class EmbedderMismatch(RuntimeError):
    """The table was built with a different embedding model."""


@dataclass
class StoreReport:
    pages_unchanged: int = 0  # page_hash matched: nothing read, embedded or written
    pages_written: int = 0  # pages with at least one change
    added: int = 0  # new chunk IDs, embedded
    reembedded: int = 0  # existing IDs whose text changed
    updated: int = 0  # same text, only metadata changed: no embedding
    deleted: int = 0  # chunks gone from their page, or from removed pages
    pages_deleted: int = 0  # pages no longer on the site

    def summary(self) -> str:
        return (
            f"pages: {self.pages_written} written, {self.pages_unchanged} unchanged,"
            f" {self.pages_deleted} removed\n"
            f"chunks: {self.added} added, {self.reembedded} re-embedded,"
            f" {self.updated} metadata only, {self.deleted} deleted"
        )


def ensure_schema(conn: psycopg.Connection, embedder: Embedder, *, reset: bool = False) -> None:
    """Create the tables if needed and check they match the embedder.
    reset=True drops all chunks first (a full rebuild)."""
    with conn.transaction():
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ingest_meta (key text PRIMARY KEY, value text NOT NULL)"
        )
        if reset:
            conn.execute("DROP TABLE IF EXISTS chunks")
            conn.execute("DELETE FROM ingest_meta")
        meta = dict(conn.execute("SELECT key, value FROM ingest_meta").fetchall())
        expected = {"embed_model": embedder.model_name, "embed_dim": str(embedder.dim)}
        if meta and meta != expected:
            raise EmbedderMismatch(
                f"chunks were embedded with {meta.get('embed_model')} ({meta.get('embed_dim')}"
                f" dims), not {embedder.model_name} ({embedder.dim} dims). Re-run with --reset."
            )
        # dim is an int from the embedder, never user text, so formatting it in is safe.
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS chunks (
              id           text PRIMARY KEY,
              url          text NOT NULL,
              anchor       text,
              title        text,
              breadcrumb   text NOT NULL,
              body         text NOT NULL,
              position     int NOT NULL,
              doc_version  text,
              page_hash    text NOT NULL,
              chunk_hash   text NOT NULL,
              embedding    vector({int(embedder.dim)}) NOT NULL,
              -- Computed by Postgres, so it can never drift from the text.
              -- 'english' stems words ("creating" matches "create").
              tsv          tsvector GENERATED ALWAYS AS
                             (to_tsvector('english', breadcrumb || ' ' || body)) STORED,
              ingested_at  timestamptz NOT NULL DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_url ON chunks (url)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS chunks_embedding ON chunks"
            " USING hnsw (embedding vector_cosine_ops)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv)")
        conn.cursor().executemany(
            "INSERT INTO ingest_meta (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            list(expected.items()),
        )
    # Needs the extension to exist, so it comes after the transaction above.
    register_vector(conn)


def page_hash(chunks: Sequence[Chunk], doc_version: str | None) -> str:
    """Changes when anything stored for the page would change. Built from the
    chunks rather than the raw HTML, so a new build timestamp or a reshuffled
    sidebar does not count as a change."""
    parts = [doc_version or ""]
    parts += [f"{c.id} {c.chunk_hash} {c.anchor} {c.title} {c.position}" for c in chunks]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def sync_page(
    conn: psycopg.Connection,
    embedder: Embedder,
    url: str,
    chunks: Sequence[Chunk],
    report: StoreReport,
    *,
    doc_version: str | None = None,
) -> None:
    """Make the stored chunks for url match chunks. An empty list removes the page."""
    new_hash = page_hash(chunks, doc_version)
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, chunk_hash, page_hash FROM chunks WHERE url = %s", (url,)
        ).fetchall()
        stored = {chunk_id: chunk_hash for chunk_id, chunk_hash, _ in rows}
        if not chunks and not rows:
            return  # nothing stored and nothing to store
        if chunks and {page for _, _, page in rows} == {new_hash}:
            report.pages_unchanged += 1
            return

        to_embed = [c for c in chunks if stored.get(c.id) != c.chunk_hash]
        same_text = [c for c in chunks if stored.get(c.id) == c.chunk_hash]
        vectors = embedder.embed_documents([c.embed_text for c in to_embed])

        conn.cursor().executemany(
            """
            INSERT INTO chunks (id, url, anchor, title, breadcrumb, body, position,
                                doc_version, page_hash, chunk_hash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              url = excluded.url, anchor = excluded.anchor, title = excluded.title,
              breadcrumb = excluded.breadcrumb, body = excluded.body,
              position = excluded.position, doc_version = excluded.doc_version,
              page_hash = excluded.page_hash, chunk_hash = excluded.chunk_hash,
              embedding = excluded.embedding, ingested_at = now()
            """,
            [
                (c.id, url, c.anchor, c.title, c.breadcrumb, c.body, c.position,
                 doc_version, new_hash, c.chunk_hash, Vector(v))
                for c, v in zip(to_embed, vectors, strict=True)
            ],
        )  # fmt: skip
        conn.cursor().executemany(
            """
            UPDATE chunks SET anchor = %s, title = %s, position = %s, doc_version = %s,
                              page_hash = %s, ingested_at = now()
            WHERE id = %s
            """,
            [(c.anchor, c.title, c.position, doc_version, new_hash, c.id) for c in same_text],
        )
        gone = set(stored) - {c.id for c in chunks}
        if gone:
            conn.execute("DELETE FROM chunks WHERE id = ANY(%s)", (list(gone),))

    report.pages_written += 1
    report.added += sum(1 for c in to_embed if c.id not in stored)
    report.reembedded += sum(1 for c in to_embed if c.id in stored)
    report.updated += len(same_text)
    report.deleted += len(gone)


def stored_urls(conn: psycopg.Connection) -> set[str]:
    return {url for (url,) in conn.execute("SELECT DISTINCT url FROM chunks")}


def delete_pages(conn: psycopg.Connection, urls: Iterable[str], report: StoreReport) -> None:
    urls = list(urls)
    if not urls:
        return
    with conn.transaction():
        cursor = conn.execute("DELETE FROM chunks WHERE url = ANY(%s)", (urls,))
    report.pages_deleted += len(urls)
    report.deleted += cursor.rowcount
