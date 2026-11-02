from prepare.ingest.crawl import crawl
from prepare.ingest.discover import extract_links
from prepare.ingest.tests.conftest import FakeFetcher, html, urlset, xml
from prepare.ingest.urls import Scope

SITE = "https://docs.example.com"


def run(routes, start=f"{SITE}/docs/", **scope_kwargs):
    fetcher = FakeFetcher(routes)
    result = crawl(Scope(start, **scope_kwargs), fetcher, sleep=lambda s: None)
    return result, fetcher


def page_urls(result):
    return [p.url for p in result.pages]


def test_uses_sitemap_and_skips_out_of_scope_urls():
    routes = {
        f"{SITE}/sitemap.xml": urlset(
            f"{SITE}/docs/a/", f"{SITE}/docs/b/", f"{SITE}/blog/post/", "https://other.com/x"
        ),
        f"{SITE}/docs/a/": html("A"),
        f"{SITE}/docs/b/": html("B"),
    }
    result, _ = run(routes)
    assert page_urls(result) == [f"{SITE}/docs/a/", f"{SITE}/docs/b/"]
    assert result.report.source == f"sitemap {SITE}/sitemap.xml"
    assert result.report.skipped == {"outside path prefix": 1, "other host": 1}


def test_follows_nested_sitemap_index():
    index = (
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>{SITE}/sitemap-0.xml</loc></sitemap></sitemapindex>"
    )
    routes = {
        f"{SITE}/sitemap-index.xml": xml(index),
        f"{SITE}/sitemap-0.xml": urlset(f"{SITE}/docs/a/"),
        f"{SITE}/docs/a/": html("A"),
    }
    result, _ = run(routes)
    assert page_urls(result) == [f"{SITE}/docs/a/"]


def test_follows_links_when_there_is_no_sitemap():
    routes = {
        f"{SITE}/docs/": html(links=("a/", "/docs/b/#part", "/pricing", "https://other.com/")),
        f"{SITE}/docs/a/": html(links=("../b/?utm_source=nav",)),
        f"{SITE}/docs/b/": html("B"),
    }
    result, _ = run(routes)
    assert page_urls(result) == [f"{SITE}/docs/", f"{SITE}/docs/a/", f"{SITE}/docs/b/"]
    assert [p.depth for p in result.pages] == [0, 1, 1]
    assert result.report.source.startswith("links from")


def test_respects_robots_txt():
    routes = {
        f"{SITE}/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /docs/private/\n"),
        f"{SITE}/sitemap.xml": urlset(f"{SITE}/docs/a/", f"{SITE}/docs/private/x/"),
        f"{SITE}/docs/a/": html("A"),
    }
    result, fetcher = run(routes)
    assert page_urls(result) == [f"{SITE}/docs/a/"]
    assert f"{SITE}/docs/private/x/" not in fetcher.requested
    assert result.report.skipped == {"robots.txt": 1}


def test_unreadable_robots_txt_aborts_the_crawl():
    routes = {f"{SITE}/robots.txt": (503, "text/plain", "")}
    result, fetcher = run(routes)
    assert result.pages == [] and "robots.txt could not be read" in result.report.aborted
    assert fetcher.requested == [f"{SITE}/robots.txt"]


def test_redirect_out_of_scope_is_dropped_and_duplicates_are_kept_once():
    routes = {
        f"{SITE}/sitemap.xml": urlset(f"{SITE}/docs/a", f"{SITE}/docs/a/", f"{SITE}/docs/gone/"),
        f"{SITE}/docs/a": ("redirect", f"{SITE}/docs/a/"),
        f"{SITE}/docs/a/": html("A"),
        f"{SITE}/docs/gone/": ("redirect", f"{SITE}/login/"),
        f"{SITE}/login/": html("Sign in"),
    }
    result, _ = run(routes)
    assert page_urls(result) == [f"{SITE}/docs/a/"]
    assert result.report.skipped == {"duplicate": 1, "redirected, outside path prefix": 1}


def test_failures_and_non_html_are_reported():
    routes = {
        f"{SITE}/sitemap.xml": urlset(f"{SITE}/docs/a/", f"{SITE}/docs/missing/", f"{SITE}/docs/x"),
        f"{SITE}/docs/a/": html("A"),
        f"{SITE}/docs/x": (200, "application/octet-stream", "binary"),
    }
    result, _ = run(routes)
    assert page_urls(result) == [f"{SITE}/docs/a/"]
    assert result.report.failed == [(f"{SITE}/docs/missing/", "HTTP 404")]
    assert result.report.skipped == {"not html": 1}


def test_stops_at_max_pages():
    urls = [f"{SITE}/docs/{i}/" for i in range(5)]
    routes = {f"{SITE}/sitemap.xml": urlset(*urls), **{u: html(u) for u in urls}}
    result, _ = run(routes, max_pages=2)
    assert len(result.pages) == 2
    assert result.report.not_fetched == 3
    assert "stopped at max_pages" in result.report.summary()


def test_extract_links_resolves_base_and_ignores_non_pages():
    page = (
        '<base href="https://docs.example.com/v2/">'
        '<a href="guide/">g</a><a href="#top">t</a><a href="mailto:x@y.z">m</a>'
    )
    assert extract_links(page, "https://docs.example.com/") == [
        "https://docs.example.com/v2/guide/"
    ]
