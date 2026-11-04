import pytest

from prepare.ingest.chunk import Chunk, embed_text
from prepare.ingest.store import (
    EmbedderMismatch,
    StoreReport,
    delete_pages,
    ensure_schema,
    sync_page,
)
from prepare.ingest.tests.conftest import FakeEmbedder

URL = "https://docs.example.com/docs/groups/"


def chunk(chunk_id: str, body: str, *, anchor=None, position=0, url=URL) -> Chunk:
    breadcrumb = "Groups"
    return Chunk(
        id=chunk_id,
        url=url,
        anchor=anchor,
        title="Groups",
        breadcrumb=breadcrumb,
        body=body,
        chunk_hash=f"hash-of-{body}",
        position=position,
    )


@pytest.fixture
def store(db):
    embedder = FakeEmbedder()
    ensure_schema(db, embedder)
    return db, embedder


def sync(store, chunks, url=URL, **kwargs) -> StoreReport:
    conn, embedder = store
    report = StoreReport()
    sync_page(conn, embedder, url, chunks, report, **kwargs)
    return report


def rows(conn):
    return conn.execute(
        "SELECT id, anchor, position, body, embedding IS NOT NULL FROM chunks ORDER BY id"
    ).fetchall()


def test_first_sync_embeds_and_stores_every_chunk(store):
    conn, embedder = store
    report = sync(store, [chunk("a", "alpha"), chunk("b", "beta", position=1)])
    assert (report.added, report.pages_written) == (2, 1)
    assert embedder.embedded == [embed_text("Groups", "alpha"), embed_text("Groups", "beta")]
    assert rows(conn) == [("a", None, 0, "alpha", True), ("b", None, 1, "beta", True)]


def test_same_chunks_again_changes_nothing(store):
    conn, embedder = store
    chunks = [chunk("a", "alpha"), chunk("b", "beta", position=1)]
    sync(store, chunks)
    before = conn.execute("SELECT id, ingested_at FROM chunks ORDER BY id").fetchall()
    embedder.embedded.clear()

    report = sync(store, chunks)
    assert report.pages_unchanged == 1
    assert (report.added, report.reembedded, report.updated, report.deleted) == (0, 0, 0, 0)
    assert embedder.embedded == []
    assert conn.execute("SELECT id, ingested_at FROM chunks ORDER BY id").fetchall() == before


def test_only_edited_chunks_are_re_embedded(store):
    conn, embedder = store
    sync(store, [chunk("a", "alpha"), chunk("b", "beta", position=1)])
    embedder.embedded.clear()

    report = sync(store, [chunk("a", "alpha"), chunk("b", "beta, edited", position=1)])
    assert (report.reembedded, report.updated) == (1, 1)
    assert embedder.embedded == [embed_text("Groups", "beta, edited")]


def test_metadata_change_updates_without_embedding(store):
    conn, embedder = store
    sync(store, [chunk("a", "alpha")])
    embedder.embedded.clear()

    report = sync(store, [chunk("a", "alpha", anchor="intro", position=3)])
    assert (report.updated, report.reembedded, report.pages_written) == (1, 0, 1)
    assert embedder.embedded == []
    assert rows(conn) == [("a", "intro", 3, "alpha", True)]


def test_doc_version_change_counts_as_a_change(store):
    conn, _ = store
    sync(store, [chunk("a", "alpha")], doc_version="v1")
    report = sync(store, [chunk("a", "alpha")], doc_version="v2")
    assert report.updated == 1
    assert conn.execute("SELECT doc_version FROM chunks").fetchone() == ("v2",)


def test_chunks_gone_from_the_page_are_deleted(store):
    conn, _ = store
    sync(store, [chunk("a", "alpha"), chunk("b", "beta", position=1)])
    report = sync(store, [chunk("a", "alpha")])
    assert report.deleted == 1
    assert [r[0] for r in rows(conn)] == ["a"]


def test_empty_chunk_list_removes_the_page(store):
    conn, _ = store
    sync(store, [chunk("a", "alpha")])
    assert sync(store, []).deleted == 1
    assert rows(conn) == []
    # Nothing stored and nothing to store: not counted as a write.
    assert sync(store, []).pages_written == 0


def test_delete_pages_leaves_other_pages(store):
    conn, _ = store
    other = "https://docs.example.com/docs/users/"
    sync(store, [chunk("a", "alpha")])
    sync(store, [chunk("u", "users", url=other)], url=other)
    report = StoreReport()
    delete_pages(conn, [URL], report)
    assert (report.pages_deleted, report.deleted) == (1, 1)
    assert [r[0] for r in rows(conn)] == ["u"]


def test_full_text_column_is_generated_and_stemmed(store):
    conn, _ = store
    sync(store, [chunk("a", "Creating groups requires admin rights")])
    (hit,) = conn.execute(
        "SELECT id FROM chunks WHERE tsv @@ plainto_tsquery('english', 'create group')"
    ).fetchone()
    assert hit == "a"


def test_different_embedding_model_is_refused(store):
    conn, _ = store

    class OtherModel(FakeEmbedder):
        model_name = "other/model"

    with pytest.raises(EmbedderMismatch, match="--reset"):
        ensure_schema(conn, OtherModel())


def test_reset_rebuilds_for_a_new_model(store):
    conn, _ = store
    sync(store, [chunk("a", "alpha")])

    class BiggerModel(FakeEmbedder):
        model_name = "bigger/model"
        dim = 8

    ensure_schema(conn, BiggerModel(), reset=True)
    assert rows(conn) == []
    meta = dict(conn.execute("SELECT key, value FROM ingest_meta").fetchall())
    assert meta == {"embed_model": "bigger/model", "embed_dim": "8"}
