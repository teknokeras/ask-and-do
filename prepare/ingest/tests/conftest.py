from pathlib import Path

import pytest

from prepare.ingest.fetcher import FetchResult

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Serves canned responses by URL. Unknown URLs get a 404.

    routes maps a URL to (status, content_type, text) or to a
    ("redirect", target_url) pair that is resolved like a followed redirect.
    """

    def __init__(self, routes: dict[str, tuple]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.requested.append(url)
        route = self.routes.get(url)
        final = url
        if route and route[0] == "redirect":
            final = route[1]
            route = self.routes.get(final)
        if route is None:
            return FetchResult(url, final, 404, "text/html", "not found")
        status, content_type, text = route
        return FetchResult(url, final, status, content_type, text)


def html(body: str = "", links: tuple[str, ...] = ()) -> tuple[int, str, str]:
    anchors = "".join(f'<a href="{link}">link</a>' for link in links)
    return 200, "text/html; charset=utf-8", f"<html><body>{body}{anchors}</body></html>"


def xml(text: str) -> tuple[int, str, str]:
    return 200, "application/xml", text


def urlset(*urls: str) -> tuple[int, str, str]:
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return xml(f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>')


@pytest.fixture
def fixture_html():
    return lambda name: (FIXTURES / name).read_text()
