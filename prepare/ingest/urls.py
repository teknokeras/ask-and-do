"""URL normalization and crawl scope: which URLs count as the same page and
which ones the crawler may visit.

Two jobs:
- normalize() gives every URL one canonical spelling, so the crawler can tell
  that "https://Docs.example.com/a#intro" and "https://docs.example.com/a" are
  the same page and fetch it once.
- Scope describes the part of a site we want (same host, under a path prefix,
  minus exclude patterns) and explains why any URL falls outside it. The reason
  goes into the crawl report.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track where a click came from. They never change
# the page content, so we strip them before comparing URLs.
TRACKING_PARAMS = re.compile(r"^(utm_\w+|gclid|fbclid|msclkid|mc_cid|mc_eid|ref_src)$", re.I)

# Links ending in these extensions point at files, not doc pages. Skipping them
# by name avoids downloading images, archives and assets just to discard them.
SKIPPED_EXTENSIONS = (
    ".pdf", ".zip", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".ico", ".css", ".js", ".json", ".xml", ".txt", ".mp4", ".webm", ".woff", ".woff2",
)  # fmt: skip

# ":443" on https and ":80" on http are implied, so "host:443" equals "host".
DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize(url: str) -> str:
    """Canonical form used to dedupe: lowercase scheme and host, no default port,
    no fragment, no tracking parameters, remaining parameters sorted."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    # Host names are case-insensitive; paths are not, so only the host is lowercased.
    host = (parts.hostname or "").lower()
    if parts.port and parts.port != DEFAULT_PORTS.get(scheme):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    # Keep real parameters (they can select different content) but drop tracking
    # ones, and sort so "?b=2&a=1" and "?a=1&b=2" compare equal.
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
               if not TRACKING_PARAMS.match(k))
    )  # fmt: skip
    # The fragment ("#section") is the last field; passing "" removes it, because
    # fragments point inside a page and never change what the server returns.
    # The trailing slash is left alone: "/page" and "/page/" may be different
    # resources, and redirects between them are handled by the crawler instead.
    return urlunsplit((scheme, host, path, query, ""))


@dataclass
class Scope:
    """The part of a site to crawl: same host as start_url, under path_prefix."""

    start_url: str
    path_prefix: str | None = None  # defaults to the start URL's path
    exclude: list[str] = field(default_factory=list)  # regexes matched against the URL
    max_pages: int = 500  # hard cap on page fetches, so a bad config cannot run forever
    max_depth: int = 10  # only used when following links

    def __post_init__(self) -> None:
        self.start_url = normalize(self.start_url)
        start = urlsplit(self.start_url)
        # origin is "scheme://host[:port]". A URL must match it exactly to be in scope,
        # so http vs https or a different subdomain counts as another site.
        self.origin = f"{start.scheme}://{start.netloc}"
        # Starting at https://docs.astral.sh/uv/ means "only pages under /uv".
        # Stored as "/uv" (leading slash, no trailing slash) for easy comparison.
        prefix = self.path_prefix if self.path_prefix is not None else start.path
        self.path_prefix = "/" + prefix.strip("/")
        self._exclude = [re.compile(p) for p in self.exclude]

    def why_out_of_scope(self, url: str) -> str | None:
        """None when the URL is in scope, otherwise a short reason for the report."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return "not http"
        if f"{parts.scheme}://{parts.netloc}" != self.origin:
            return "other host"
        path = parts.path or "/"
        prefix = self.path_prefix.rstrip("/")
        # Match whole path segments: prefix "/uv" allows "/uv" and "/uv/..." but
        # not "/uvx/...". An empty prefix (crawl from "/") allows every path.
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            return "outside path prefix"
        if path.lower().endswith(SKIPPED_EXTENSIONS):
            return "not a page"
        # User-supplied patterns, e.g. r"/(zh-cn|\d+\.\d+)/" to skip translations
        # and old versions that would otherwise duplicate the current docs.
        if any(p.search(url) for p in self._exclude):
            return "excluded"
        return None
