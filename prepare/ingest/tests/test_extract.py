from prepare.ingest.extract import extract

URL = "https://docs.example.com/docs/groups/create/"


def test_keeps_article_and_drops_navigation(fixture_html):
    page = extract(fixture_html("docs_page.html"), URL)
    assert page is not None
    for chrome in ("SITE-HEADER-NAV", "SIDEBAR-LINK-TEXT", "ON-THIS-PAGE-LIST", "FOOTER-COPYRIGHT"):
        assert chrome not in page.markdown
    assert "Creating a group" in page.title


def test_keeps_headings_code_blocks_and_tables(fixture_html):
    md = extract(fixture_html("docs_page.html"), URL).markdown
    assert "## Using the API" in md
    assert 'curl -X POST https://example.test/api/groups \\\n  -H "Authorization' in md
    assert "Members per group" in md and "500" in md


def test_flags_pages_where_little_was_extracted(fixture_html):
    good = extract(fixture_html("docs_page.html"), URL)
    assert not good.low_coverage
    thin = extract(fixture_html("nav_only.html"), URL)
    assert thin is None or thin.low_coverage
