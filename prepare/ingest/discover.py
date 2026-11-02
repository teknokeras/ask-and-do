"""Finding pages: robots.txt rules, sitemaps (including sitemap indexes), and
links inside a page for sites without a sitemap.

The crawler asks three questions here:
1. What does the site allow us to fetch? (load_robots)
2. Does the site publish a list of its pages? (sitemap_candidates, read_sitemap)
3. If not, which pages does this page link to? (extract_links)
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from prepare.ingest.fetcher import USER_AGENT, Fetcher

# A sitemap index can point to more sitemaps, which can point to more. This
# stops a broken or hostile site from sending us around forever.
MAX_SITEMAP_FILES = 50


@dataclass
class Robots:
    """The site's robots.txt rules, in one of three states:
    - parser set: follow the rules in the file
    - parser None, is_not_allowed None: no robots.txt, everything allowed
    - is_not_allowed set: robots.txt could not be read, nothing allowed
    """

    parser: RobotFileParser | None  # None means allow everything
    # Set to the reason (e.g. "HTTP 503") when robots.txt could not be read: allow nothing.
    is_not_allowed: str | None = None
    sitemaps: list[str] = field(default_factory=list)  # "Sitemap:" lines in robots.txt

    def allowed(self, url: str) -> bool:
        if self.is_not_allowed:
            return False
        return self.parser is None or self.parser.can_fetch(USER_AGENT, url)

    @property
    def crawl_delay(self) -> float | None:
        """Seconds the site asks crawlers to wait between requests, if it says."""
        if self.parser is None:
            return None
        delay = self.parser.crawl_delay(USER_AGENT)
        return float(delay) if delay is not None else None


def load_robots(fetcher: Fetcher, origin: str) -> Robots:
    """Follows RFC 9309: a 4xx means no rules; a 5xx or network error means
    assume everything is disallowed."""
    result = fetcher.fetch(f"{origin}/robots.txt")
    # 404 and friends: the site has no robots.txt, so there are no restrictions.
    if 400 <= result.status < 500:
        return Robots(parser=None)
    # Server error or network failure: we cannot know the rules, so the safe
    # choice (and the one the standard asks for) is to crawl nothing.
    if not result.ok:
        return Robots(parser=None, is_not_allowed=result.error or f"HTTP {result.status}")
    parser = RobotFileParser()
    parser.parse(result.text.splitlines())
    return Robots(parser=parser, sitemaps=list(parser.site_maps() or []))


def sitemap_candidates(origin: str, path_prefix: str, robots: Robots) -> list[str]:
    """Places a sitemap might be, best guess first. The crawler uses the first
    one that actually lists pages."""
    prefix = path_prefix.rstrip("/")
    # 1. Sitemaps the site names in robots.txt (only on our own host).
    candidates = [s for s in robots.sitemaps if s.startswith(origin + "/")]
    # 2. A sitemap inside the prefix: docs hosted under a sub-path, like
    #    docs.astral.sh/uv/, often have their own sitemap there.
    if prefix:
        candidates.append(f"{origin}{prefix}/sitemap.xml")
    # 3. The common names at the site root.
    candidates += [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml",
                   f"{origin}/sitemap-index.xml"]  # fmt: skip
    # Remove duplicates but keep the order.
    return list(dict.fromkeys(candidates))


def read_sitemap(fetcher: Fetcher, url: str) -> list[str]:
    """Page URLs listed in a sitemap, following nested sitemap indexes.
    Returns an empty list if the URL is not a readable sitemap."""
    pages: list[str] = []
    pending, visited = [url], set()
    while pending and len(visited) < MAX_SITEMAP_FILES:
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        result = fetcher.fetch(current)
        if not result.ok:
            continue
        try:
            root = ET.fromstring(result.text.encode("utf-8"))
        except ET.ParseError:
            # Some sites return an HTML "not found" page with status 200.
            continue
        # Both sitemap kinds keep their URLs in <loc> elements.
        locs = [el.text.strip() for el in root.iter() if _local(el.tag) == "loc" and el.text]
        if _local(root.tag) == "sitemapindex":
            # An index lists other sitemap files: read those next.
            pending.extend(locs)
        elif _local(root.tag) == "urlset":
            # A regular sitemap lists pages.
            pages.extend(locs)
    return pages


def _local(tag: str) -> str:
    """Tag name without its XML namespace: "{http://...}loc" -> "loc"."""
    return tag.rsplit("}", 1)[-1]


def extract_links(html: str, page_url: str) -> list[str]:
    """Absolute URLs of <a href> links, resolved against <base href> if present."""
    parser = _LinkParser()
    parser.feed(html)
    # Relative links are resolved against <base href> when the page sets one,
    # otherwise against the page's own URL.
    base = urljoin(page_url, parser.base) if parser.base else page_url
    links = []
    for href in parser.hrefs:
        href = href.strip()
        # In-page anchors, email, script and phone links are never doc pages.
        if href and not href.startswith(("#", "mailto:", "javascript:", "tel:")):
            links.append(urljoin(base, href))
    return links


class _LinkParser(HTMLParser):
    """Collects <a href> values and the first <base href>. Uses the standard
    library parser, which copes with messy real-world HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.base: str | None = None
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if not href:
            return
        if tag == "a":
            self.hrefs.append(href)
        elif tag == "base" and self.base is None:
            self.base = href
