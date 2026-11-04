from prepare.ingest.pipeline import ingest
from prepare.ingest.store import StoreReport, sync_page
from prepare.ingest.tests.conftest import FakeEmbedder, FakeFetcher, urlset
from prepare.ingest.urls import Scope

SITE = "https://docs.example.com"
GROUPS = f"{SITE}/docs/groups/"
USERS = f"{SITE}/docs/users/"


def page(fixture_html, title: str = "Creating a group", note: str = "") -> tuple[int, str, str]:
    html = fixture_html("docs_page.html").replace("Creating a group", title)
    if note:
        html = html.replace("</article>", f"<p>{note}</p></article>")
    return 200, "text/html; charset=utf-8", html


def run(db, embedder, routes, **kwargs):
    return ingest(
        Scope(f"{SITE}/docs/"), FakeFetcher(routes), embedder, db, sleep=lambda s: None, **kwargs
    )


def stored(db):
    return db.execute(
        "SELECT url, breadcrumb, anchor FROM chunks ORDER BY url, position"
    ).fetchall()


def test_ingest_stores_the_article_without_navigation(db, fixture_html):
    routes = {f"{SITE}/sitemap.xml": urlset(GROUPS), GROUPS: page(fixture_html)}
    report = run(db, FakeEmbedder(), routes)
    assert report.store.added == report.chunks > 0
    # The fixture's sections are all small, so they merge into the h1 section,
    # which has no id: one chunk that cites the page itself.
    assert stored(db) == [(GROUPS, "Creating a group", None)]
    (body,) = db.execute("SELECT body FROM chunks").fetchone()
    assert "curl -X POST" in body and "| Members per group | 500 |" in body
    for nav in ("SITE-HEADER-NAV", "SIDEBAR-LINK-TEXT", "ON-THIS-PAGE-LIST", "FOOTER-COPYRIGHT"):
        assert nav not in body


def test_second_run_changes_nothing(db, fixture_html):
    routes = {
        f"{SITE}/sitemap.xml": urlset(GROUPS, USERS),
        GROUPS: page(fixture_html),
        USERS: page(fixture_html, "Inviting a user"),
    }
    embedder = FakeEmbedder()
    run(db, embedder, routes)
    before = db.execute("SELECT id, chunk_hash, ingested_at FROM chunks ORDER BY id").fetchall()
    embedder.embedded.clear()

    report = run(db, embedder, routes)
    assert report.store.pages_unchanged == 2
    assert report.store.pages_written == report.store.deleted == 0
    assert embedder.embedded == []
    assert (
        db.execute("SELECT id, chunk_hash, ingested_at FROM chunks ORDER BY id").fetchall()
        == before
    )


def test_edited_page_re_embeds_only_its_changed_chunks(db, fixture_html):
    routes = {
        f"{SITE}/sitemap.xml": urlset(GROUPS, USERS),
        GROUPS: page(fixture_html),
        USERS: page(fixture_html, "Inviting a user"),
    }
    embedder = FakeEmbedder()
    run(db, embedder, routes)
    embedder.embedded.clear()

    routes[GROUPS] = page(fixture_html, note="Group names cannot be changed after creation.")
    report = run(db, embedder, routes)
    assert (report.store.pages_written, report.store.pages_unchanged) == (1, 1)
    assert 0 < len(embedder.embedded) < report.chunks
    assert all("Inviting a user" not in text for text in embedder.embedded)


def test_removed_page_is_deleted(db, fixture_html):
    routes = {
        f"{SITE}/sitemap.xml": urlset(GROUPS, USERS),
        GROUPS: page(fixture_html),
        USERS: page(fixture_html, "Inviting a user"),
    }
    embedder = FakeEmbedder()
    run(db, embedder, routes)

    routes[f"{SITE}/sitemap.xml"] = urlset(GROUPS)
    report = run(db, embedder, routes)
    assert report.store.pages_deleted == 1
    assert {url for url, _, _ in stored(db)} == {GROUPS}


def test_page_that_fails_to_fetch_keeps_its_chunks(db, fixture_html):
    routes = {
        f"{SITE}/sitemap.xml": urlset(GROUPS, USERS),
        GROUPS: page(fixture_html),
        USERS: page(fixture_html, "Inviting a user"),
    }
    embedder = FakeEmbedder()
    run(db, embedder, routes)

    routes[USERS] = (404, "text/html", "gone for now")
    report = run(db, embedder, routes)
    assert report.store.pages_deleted == 0
    assert {url for url, _, _ in stored(db)} == {GROUPS, USERS}


def test_no_deletes_when_crawl_hits_max_pages(db, fixture_html):
    routes = {
        f"{SITE}/sitemap.xml": urlset(GROUPS, USERS),
        GROUPS: page(fixture_html),
        USERS: page(fixture_html, "Inviting a user"),
    }
    embedder = FakeEmbedder()
    run(db, embedder, routes)

    report = ingest(
        Scope(f"{SITE}/docs/", max_pages=1), FakeFetcher(routes), embedder, db,
        sleep=lambda s: None,
    )  # fmt: skip
    assert report.cleanup_skipped == "crawl stopped at max_pages"
    assert {url for url, _, _ in stored(db)} == {GROUPS, USERS}


def test_pages_from_another_site_are_never_deleted(db, fixture_html):
    embedder = FakeEmbedder()
    run(db, embedder, {f"{SITE}/sitemap.xml": urlset(GROUPS), GROUPS: page(fixture_html)})
    other = "https://other.example.com/guide/"
    from prepare.ingest.chunk import Chunk

    sync_page(
        db, embedder, other,
        [Chunk("x", other, None, "Guide", "Guide", "other site text", "h", 0)],
        StoreReport(),
    )  # fmt: skip
    run(db, embedder, {f"{SITE}/sitemap.xml": urlset(GROUPS), GROUPS: page(fixture_html)})
    assert {url for url, _, _ in stored(db)} == {GROUPS, other}


def test_page_without_article_body_is_reported_and_removed(db, fixture_html):
    routes = {f"{SITE}/sitemap.xml": urlset(GROUPS), GROUPS: page(fixture_html)}
    embedder = FakeEmbedder()
    run(db, embedder, routes)

    routes[GROUPS] = (200, "text/html", "<html><body></body></html>")
    report = run(db, embedder, routes)
    assert report.no_body == [GROUPS]
    assert stored(db) == []


def test_aborted_crawl_touches_nothing(db, fixture_html):
    routes = {f"{SITE}/sitemap.xml": urlset(GROUPS), GROUPS: page(fixture_html)}
    embedder = FakeEmbedder()
    run(db, embedder, routes)

    routes[f"{SITE}/robots.txt"] = (503, "text/plain", "down")
    report = run(db, embedder, routes)
    assert report.crawl.aborted
    assert {url for url, _, _ in stored(db)} == {GROUPS}
