"""The crawl loop: read robots.txt, take URLs from a sitemap (or follow links
when there is none), fetch politely, and keep in-scope HTML pages.

This file only decides what to fetch and what to keep. How to fetch lives in
fetcher.py, how to find URLs in discover.py, and what counts as in scope in
urls.py. The output is raw HTML pages plus a report; extract.py turns the
pages into Markdown afterwards.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from prepare.ingest.discover import (
    extract_links,
    load_robots,
    read_sitemap,
    sitemap_candidates,
)
from prepare.ingest.fetcher import Fetcher
from prepare.ingest.report import CrawlReport
from prepare.ingest.urls import Scope, normalize


@dataclass
class CrawledPage:
    url: str  # normalized final URL, after redirects
    html: str
    depth: int  # link depth from the start URL; 0 for sitemap URLs


@dataclass
class CrawlResult:
    pages: list[CrawledPage] = field(default_factory=list)
    report: CrawlReport = field(default_factory=CrawlReport)


def crawl(
    scope: Scope,
    fetcher: Fetcher,
    *,
    delay: float = 0.5,  # seconds between page requests, to be polite to the site
    use_sitemap: bool = True,  # False forces link following, e.g. to test it
    sleep: Callable[[float], None] = time.sleep,  # tests pass a no-op
) -> CrawlResult:
    result = CrawlResult()
    report = result.report

    # Step 1: robots.txt. If we cannot read it, we stop before fetching anything.
    robots = load_robots(fetcher, scope.origin)
    if robots.is_not_allowed:
        report.aborted = (
            f"robots.txt could not be read ({robots.is_not_allowed}), so nothing is allowed"
        )
        return result
    # If the site asks for a longer pause between requests, use that instead.
    delay = max(delay, robots.crawl_delay or 0.0)

    # queue holds (url, depth) waiting to be fetched, oldest first, which makes
    # link following breadth-first (all depth-1 pages before depth-2 pages).
    queue: deque[tuple[str, int]] = deque()
    # seen holds every URL ever considered, so each one is checked only once.
    seen: set[str] = set()

    def enqueue(url: str, depth: int) -> None:
        """Queue a URL if it is new, in scope and allowed by robots.txt.
        Otherwise count the reason it was skipped."""
        url = normalize(url)
        if url in seen:
            return
        seen.add(url)
        reason = scope.why_out_of_scope(url) or (None if robots.allowed(url) else "robots.txt")
        if reason:
            report.skipped[reason] += 1
            return
        queue.append((url, depth))
        report.queued += 1

    # Step 2: find the pages. A sitemap is the site's own list, so it is preferred.
    # When one is found we fetch exactly those pages and do not follow links.
    follow_links = True
    if use_sitemap:
        for candidate in sitemap_candidates(scope.origin, scope.path_prefix, robots):
            urls = read_sitemap(fetcher, candidate)
            if urls:
                report.source = f"sitemap {candidate}"
                for url in urls:
                    enqueue(url, 0)
                follow_links = False
                break
    # No sitemap: start from the start URL and discover pages through links.
    if follow_links:
        report.source = f"links from {scope.start_url}"
        enqueue(scope.start_url, 0)

    # Step 3: fetch queued pages one at a time, until the queue is empty or the
    # page cap is reached.
    kept: set[str] = set()  # final URLs of pages already kept, to drop duplicates
    while queue and report.requests < scope.max_pages:
        url, depth = queue.popleft()
        if report.requests:  # no wait before the very first request
            sleep(delay)
        response = fetcher.fetch(url)
        report.requests += 1

        if not response.ok:
            report.failed.append((url, response.error or f"HTTP {response.status}"))
            continue

        # The page may have redirected. Check the place we landed, not the link:
        # a docs URL that redirects to /login or another site must be dropped,
        # or we would index "Sign in" pages as documentation.
        final = normalize(response.final_url)
        if final != url:
            reason = scope.why_out_of_scope(final)
            if reason is None and not robots.allowed(final):
                reason = "robots.txt"
            if reason:
                report.skipped[f"redirected, {reason}"] += 1
                continue
            # Mark the landing URL as seen so links to it are not queued again.
            seen.add(final)
        # Two different URLs can land on the same page ("/a" redirects to "/a/").
        # Keep the first copy only.
        if final in kept:
            report.skipped["duplicate"] += 1
            continue
        if not response.is_html:
            report.skipped["not html"] += 1
            continue

        kept.add(final)
        result.pages.append(CrawledPage(final, response.text, depth))
        report.pages += 1
        # Only in link-following mode: queue this page's links one level deeper.
        if follow_links and depth < scope.max_depth:
            for link in extract_links(response.text, response.final_url):
                enqueue(link, depth + 1)

    # Anything still queued was cut off by max_pages; the report says how many.
    report.not_fetched = len(queue)
    return result
