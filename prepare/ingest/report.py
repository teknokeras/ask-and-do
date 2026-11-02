"""What a crawl did, so skipped and failed pages are visible instead of silent.

The crawler fills in a CrawlReport as it goes. summary() turns it into a few
readable lines, for example:

    source: sitemap https://www.python-httpx.org/sitemap.xml
    queued 23 · fetched 23 · kept 23 pages · failed 0
    skipped: outside path prefix: 4, robots.txt: 1
"""

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class CrawlReport:
    source: str = ""  # "sitemap <url>" or "links from <url>"
    queued: int = 0  # in-scope URLs waiting to be fetched
    requests: int = 0  # page fetches made (robots.txt and sitemaps not counted)
    pages: int = 0  # HTML pages kept
    # Counted, not listed: link following can skip thousands of external links,
    # and a count per reason is enough to see what happened.
    skipped: Counter[str] = field(default_factory=Counter)  # reason -> count
    # Listed with URLs: failures are few and each one may need a look.
    failed: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)
    not_fetched: int = 0  # still queued when max_pages was reached
    aborted: str | None = None  # why the crawl stopped before starting

    def summary(self) -> str:
        if self.aborted:
            return f"crawl aborted: {self.aborted}"
        lines = [
            f"source: {self.source}",
            f"queued {self.queued} · fetched {self.requests} · kept {self.pages} pages"
            f" · failed {len(self.failed)}",
        ]
        if self.skipped:
            # Most common reason first.
            reasons = ", ".join(f"{r}: {n}" for r, n in self.skipped.most_common())
            lines.append(f"skipped: {reasons}")
        if self.not_fetched:
            lines.append(f"stopped at max_pages, {self.not_fetched} URL(s) not fetched")
        lines += [f"  failed {url}: {reason}" for url, reason in self.failed]
        return "\n".join(lines)
