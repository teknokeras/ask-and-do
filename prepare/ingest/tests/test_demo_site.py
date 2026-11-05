"""End-to-end ingest of the Larkspur demo docs (prepare/demo/docs_site).

The site is built into a temp folder by its own build script (a separate
process: ingest hands off with the demo through files, not imports) and
served from disk by a fetcher, so the crawl, extraction, chunking and store
run exactly as against a real site, minus the network.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from prepare.ingest.fetcher import FetchResult
from prepare.ingest.pipeline import ingest
from prepare.ingest.tests.conftest import FakeEmbedder
from prepare.ingest.urls import Scope

BASE = "http://localhost:8001"
PAGE_COUNT = 28
TYPES = {".html": "text/html; charset=utf-8", ".xml": "application/xml"}


class DiskFetcher:
    """Serves a built static site from a folder, like a plain web server."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch(self, url: str) -> FetchResult:
        path = self.root / url.removeprefix(BASE).split("?")[0].lstrip("/")
        if path.is_dir():
            path = path / "index.html"
        if not path.is_file():
            return FetchResult(url, url, 404, "text/html", "not found")
        return FetchResult(url, url, 200, TYPES.get(path.suffix, "text/plain"), path.read_text())


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("larkspur")
    subprocess.run(
        [sys.executable, "-m", "prepare.demo.docs_site.build", "--base-url", f"{BASE}/",
         "--out", str(out)],
        check=True, capture_output=True,
    )  # fmt: skip
    return out


def run(db, site, embedder):
    return ingest(Scope(f"{BASE}/"), DiskFetcher(site), embedder, db, sleep=lambda s: None)


def test_every_page_is_ingested_with_good_coverage(db, site):
    report = run(db, site, FakeEmbedder())
    assert report.crawl.source == f"sitemap {BASE}/sitemap.xml"
    assert report.store.pages_written == PAGE_COUNT
    assert report.low_coverage == []
    urls = {u for (u,) in db.execute("SELECT DISTINCT url FROM chunks").fetchall()}
    assert len(urls) == PAGE_COUNT


def test_chunks_hold_no_navigation_or_link_symbols(db, site):
    run(db, site, FakeEmbedder())
    bodies = [b for (b,) in db.execute("SELECT body FROM chunks").fetchall()]
    for body in bodies:
        for chrome in ("On this page", "fictional product", "Larkspur Docs", "←", "→"):
            assert chrome not in body
        assert not re.search(r"^#+ .*[#¶]$", body, re.M), body[:80]


def test_anchors_resolve_to_headings_on_the_page(db, site):
    run(db, site, FakeEmbedder())
    rows = db.execute("SELECT url, anchor FROM chunks WHERE anchor IS NOT NULL").fetchall()
    assert rows
    for url, anchor in rows:
        html = DiskFetcher(site).fetch(url).text
        assert f'id="{anchor}"' in html, f"{url}#{anchor}"


def test_tables_and_code_survive(db, site):
    run(db, site, FakeEmbedder())
    (body,) = db.execute(
        "SELECT body FROM chunks WHERE url = %s AND anchor = 'replace-a-group-put'",
        (f"{BASE}/groups/updating-groups/",),
    ).fetchone()
    assert "```" in body and '"members": ["usr_4f9a2c1e", "usr_0b7c55d2"]' in body
    (table,) = db.execute(
        "SELECT body FROM chunks WHERE url = %s AND anchor = 'create-vs-invite'",
        (f"{BASE}/users/creating-users/",),
    ).fetchone()
    assert "| Account active | Immediately | Only after the person accepts |" in table


def test_second_run_changes_nothing(db, site):
    embedder = FakeEmbedder()
    run(db, site, embedder)
    embedder.embedded.clear()
    report = run(db, site, embedder)
    assert report.store.pages_unchanged == PAGE_COUNT
    assert embedder.embedded == []
