import re
from pathlib import Path

import pytest

from prepare.demo.docs_site import build as site

PAGE_COUNT = 28


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[Path, list[site.Page]]:
    out = tmp_path_factory.mktemp("site")
    return out, site.build(out, "http://localhost:8001/")


def test_every_page_is_written_and_listed_in_the_sitemap(built):
    out, pages = built
    assert len(pages) == PAGE_COUNT
    sitemap = (out / "sitemap.xml").read_text()
    for page in pages:
        assert (out / page.path.strip("/") / "index.html").is_file()
        assert f"<loc>http://localhost:8001{page.path}</loc>" in sitemap
    assert sitemap.count("<loc>") == PAGE_COUNT


def test_robots_allows_crawling_and_points_to_the_sitemap(built):
    out, _ = built
    robots = (out / "robots.txt").read_text()
    assert "Allow: /" in robots and "Sitemap: http://localhost:8001/sitemap.xml" in robots


def test_headings_get_unique_ids_and_one_h1_per_page(built):
    out, pages = built
    for page in pages:
        html = (out / page.path.strip("/") / "index.html").read_text()
        assert html.count("<h1>") == 1
        ids = re.findall(r'<h[23] id="([^"]+)"', html)
        assert ids and len(ids) == len(set(ids)), page.path


def test_page_body_sits_in_article_and_navigation_outside_it(built):
    out, _ = built
    html = (out / "groups/updating-groups/index.html").read_text()
    article = html.split("<article>")[1].split("</article>")[0]
    assert "PUT /v1/groups/{group_id}" in article
    for chrome in ('class="sidebar"', "On this page", 'class="pager"', "fictional product"):
        assert chrome not in article and chrome in html


def test_broken_internal_links_fail_the_build(tmp_path):
    content = tmp_path / "content"
    (content / "users").mkdir(parents=True)
    (content / "users" / "a.html").write_text(
        "<!--\ntitle: A\nsection: Users\norder: 1\ndescription: d\n-->\n"
        '<h2>Hello</h2><a href="/users/missing/">x</a><a href="/users/a/#nope">y</a>'
    )
    with pytest.raises(ValueError) as exc:
        site.build(tmp_path / "out", content_dir=content)
    assert "missing page /users/missing/" in str(exc.value)
    assert "missing section /users/a/#nope" in str(exc.value)


def test_links_follow_a_base_url_with_a_path(tmp_path):
    site.build(tmp_path, "http://localhost:9000/docs/")
    html = (tmp_path / "index.html").read_text()
    assert 'href="/docs/users/creating-users/"' in html
    assert 'href="/users/' not in html
    assert "<loc>http://localhost:9000/docs/</loc>" in (tmp_path / "sitemap.xml").read_text()
