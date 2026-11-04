"""The whole ingest run: crawl, extract, chunk, embed and store, with one
report covering every step.

Removed pages are deleted only when the crawl gives a full picture of the
site. A page is treated as removed when it is stored, inside this crawl's
scope, and was not kept this time. Pages that failed to fetch keep their
chunks (a timeout is not a deletion), and nothing is deleted when the crawl
was cut off by max_pages or kept no pages at all.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg

from common.embedder import Embedder
from prepare.ingest.chunk import chunk_page
from prepare.ingest.crawl import crawl
from prepare.ingest.extract import extract
from prepare.ingest.fetcher import Fetcher
from prepare.ingest.report import CrawlReport
from prepare.ingest.store import (
    StoreReport,
    delete_pages,
    ensure_schema,
    stored_urls,
    sync_page,
)
from prepare.ingest.urls import Scope, normalize

# Stored URLs out of scope for these reasons belong to another site or another
# part of it (a second docs set in the same database), so they are never deleted.
OTHER_SITE = {"not http", "other host", "outside path prefix"}


@dataclass
class IngestReport:
    crawl: CrawlReport = field(default_factory=CrawlReport)
    store: StoreReport = field(default_factory=StoreReport)
    chunks: int = 0  # chunks produced this run, changed or not
    no_body: list[str] = field(default_factory=list)  # pages where extraction found nothing
    # (url, coverage) for pages whose extracted text is a small share of the visible
    # text: either a thin index page or extraction cut real content. Worth a look.
    low_coverage: list[tuple[str, float]] = field(default_factory=list)
    cleanup_skipped: str | None = None  # why removed pages were not deleted

    def summary(self) -> str:
        lines = [self.crawl.summary()]
        if self.crawl.aborted:
            return lines[0]
        lines.append(f"chunks this run: {self.chunks}")
        lines.append(self.store.summary())
        if self.cleanup_skipped:
            lines.append(f"removed pages not deleted: {self.cleanup_skipped}")
        lines += [f"  no article body: {url}" for url in self.no_body]
        lines += [f"  low coverage {c:.0%}: {url}" for url, c in self.low_coverage]
        return "\n".join(lines)


def ingest(
    scope: Scope,
    fetcher: Fetcher,
    embedder: Embedder,
    conn: psycopg.Connection,
    *,
    reset: bool = False,
    doc_version: str | None = None,
    delay: float = 0.5,
    use_sitemap: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> IngestReport:
    report = IngestReport()
    ensure_schema(conn, embedder, reset=reset)

    crawled = crawl(scope, fetcher, delay=delay, use_sitemap=use_sitemap, sleep=sleep)
    report.crawl = crawled.report
    if crawled.report.aborted:
        return report

    for page in crawled.pages:
        extracted = extract(page.html, page.url)
        if extracted is None:
            # The page exists but has no article body any more: an empty chunk
            # list removes what was stored for it.
            report.no_body.append(page.url)
            sync_page(conn, embedder, page.url, [], report.store, doc_version=doc_version)
            continue
        if extracted.low_coverage:
            report.low_coverage.append((page.url, extracted.coverage))
        chunks = chunk_page(extracted)
        report.chunks += len(chunks)
        sync_page(conn, embedder, page.url, chunks, report.store, doc_version=doc_version)

    if crawled.report.not_fetched:
        report.cleanup_skipped = "crawl stopped at max_pages"
    elif not crawled.pages:
        report.cleanup_skipped = "no pages were kept"
    else:
        kept = {p.url for p in crawled.pages}
        failed = {normalize(url) for url, _ in crawled.report.failed}
        removed = [
            url
            for url in stored_urls(conn)
            if url not in kept
            and url not in failed
            and scope.why_out_of_scope(url) not in OTHER_SITE
        ]
        delete_pages(conn, removed, report.store)
    return report
