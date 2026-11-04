import hashlib
import uuid
from pathlib import Path

import psycopg
import pytest

from common.settings import get_settings
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


class FakeEmbedder:
    """Deterministic 4-number vectors from a hash of the text. Records every
    text it embeds, so tests can check what was (not) re-embedded."""

    model_name = "fake/model"
    dim = 4

    def __init__(self) -> None:
        self.embedded: list[str] = []

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255 for b in digest[: self.dim]]

    def embed_documents(self, texts):
        self.embedded.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def db():
    """A connection to the dev database (just db-up), working in a throwaway
    schema so tests never touch real chunks. Skips when no database is running."""
    try:
        conn = psycopg.connect(get_settings().database_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("database not running (just db-up)")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    conn.execute(f"CREATE SCHEMA {schema}")
    # public stays on the path because the vector extension lives there.
    conn.execute(f"SET search_path TO {schema}, public")
    try:
        yield conn
    finally:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()
