"""Build the Larkspur demo docs into a static site that the ingest can crawl.

Larkspur is a fictional product. Its docs are the test corpus for ask-and-do:
we write them ourselves so the ground truth for evals is known, and they
describe the same API as the mock admin API in prepare/demo/admin_api/.

Each file in content/ is an HTML fragment with a metadata comment on top:

    <!--
    title: Inviting users
    section: Users
    order: 2
    description: One line for the meta description.
    -->
    <p>Body...</p>

The build wraps every fragment in the same layout a real docs site has
(header, sidebar, breadcrumbs, "On this page", prev/next links, footer), so
the extractor has real navigation to strip. It also writes sitemap.xml and
robots.txt, and stops on internal links that point nowhere.

    uv run python -m prepare.demo.docs_site.build --base-url http://localhost:8001/

Standard library only, so the demo runs without the project's dependencies.
"""

import argparse
import html
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).parent
CONTENT_DIR = HERE / "content"
STATIC_DIR = HERE / "static"
DEFAULT_OUT = HERE / "build"
DEFAULT_BASE_URL = "http://localhost:8001/"

SITE_NAME = "Larkspur Docs"
# Sidebar order. A page whose section is not listed here is a build error.
SECTIONS = [
    "Getting started",
    "Users",
    "Groups",
    "Projects",
    "Roles and permissions",
    "API reference",
    "Help",
]

META = re.compile(r"\A<!--\n(?P<meta>.*?)\n-->\n", re.S)
HEADING = re.compile(r"<h(?P<level>[23])>(?P<text>.*?)</h(?P=level)>", re.S)
TAG = re.compile(r"<[^>]+>")
HREF = re.compile(r'href="(/[^"]*)"')


@dataclass
class Heading:
    level: int
    text: str  # plain text, for the "On this page" list
    id: str


@dataclass
class Page:
    source: Path
    path: str  # site path without the base prefix: "/" or "/users/inviting-users/"
    title: str
    section: str
    order: int
    description: str
    body: str  # fragment with heading ids added
    headings: list[Heading] = field(default_factory=list)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_page(source: Path, content_dir: Path) -> Page:
    text = source.read_text(encoding="utf-8")
    match = META.match(text)
    if not match:
        raise ValueError(f"{source}: missing metadata comment at the top")
    meta = {}
    for line in match["meta"].splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    for key in ("title", "section", "order", "description"):
        if not meta.get(key):
            raise ValueError(f"{source}: metadata is missing '{key}'")
    if meta["section"] not in SECTIONS:
        raise ValueError(f"{source}: unknown section '{meta['section']}'")

    rel = source.relative_to(content_dir).with_suffix("")
    path = "/" if rel.as_posix() == "index" else f"/{rel.as_posix()}/"
    page = Page(
        source=source,
        path=path,
        title=meta["title"],
        section=meta["section"],
        order=int(meta["order"]),
        description=meta["description"],
        body="",
    )
    page.body = _add_heading_ids(text[match.end() :], page.headings)
    return page


def _add_heading_ids(body: str, headings: list[Heading]) -> str:
    """Give each <h2>/<h3> an id from its text (unique within the page) and a
    "#" self-link, the way doc generators do. Citations link to these ids."""
    used: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        level, inner = int(match["level"]), match["text"]
        plain = html.unescape(TAG.sub("", inner)).strip()
        base = slugify(plain) or "section"
        used[base] = used.get(base, 0) + 1
        hid = base if used[base] == 1 else f"{base}-{used[base]}"
        headings.append(Heading(level, plain, hid))
        return (
            f'<h{level} id="{hid}">{inner}'
            f'<a class="anchor" href="#{hid}" aria-label="Link to this section">#</a></h{level}>'
        )

    return HEADING.sub(replace, body)


def load_pages(content_dir: Path = CONTENT_DIR) -> list[Page]:
    pages = [load_page(p, content_dir) for p in sorted(content_dir.rglob("*.html"))]
    pages.sort(key=lambda p: (SECTIONS.index(p.section), p.order, p.path))
    return pages


def check_links(pages: list[Page]) -> list[str]:
    """Internal links ("/users/...", "/api/errors/#codes") that point to no
    page or no heading. Returned as messages; the build fails on any."""
    by_path = {p.path: p for p in pages}
    problems = []
    for page in pages:
        for href in HREF.findall(page.body):
            target, _, anchor = href.partition("#")
            linked = by_path.get(target)
            if linked is None:
                problems.append(f"{page.source.name}: link to missing page {href}")
            elif anchor and anchor not in {h.id for h in linked.headings}:
                problems.append(f"{page.source.name}: link to missing section {href}")
    return problems


def build(out_dir: Path = DEFAULT_OUT, base_url: str = DEFAULT_BASE_URL,
          content_dir: Path = CONTENT_DIR) -> list[Page]:  # fmt: skip
    """Write the site to out_dir (replacing it) and return the pages."""
    if not base_url.endswith("/"):
        base_url += "/"
    prefix = urlsplit(base_url).path.rstrip("/")  # "" at the root, "/docs" under /docs/

    pages = load_pages(content_dir)
    problems = check_links(pages)
    if problems:
        raise ValueError("broken internal links:\n  " + "\n  ".join(problems))

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    shutil.copytree(STATIC_DIR, out_dir / "static")

    for i, page in enumerate(pages):
        prev_page = pages[i - 1] if i > 0 else None
        next_page = pages[i + 1] if i + 1 < len(pages) else None
        target = out_dir / page.path.strip("/") / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(page, pages, prev_page, next_page, prefix), encoding="utf-8")

    (out_dir / "sitemap.xml").write_text(render_sitemap(pages, base_url), encoding="utf-8")
    (out_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {base_url}sitemap.xml\n", encoding="utf-8"
    )
    return pages


def render(page: Page, pages: list[Page], prev_page: Page | None, next_page: Page | None,
           prefix: str) -> str:  # fmt: skip
    esc = html.escape

    def url(path: str) -> str:
        return prefix + path

    body = HREF.sub(lambda m: f'href="{url(m[1])}"', page.body)

    nav = []
    for section in SECTIONS:
        items = []
        for p in pages:
            if p.section != section:
                continue
            current = ' aria-current="page"' if p is page else ""
            items.append(f'<li><a href="{url(p.path)}"{current}>{esc(p.title)}</a></li>')
        nav.append(
            f'<li class="nav-section"><span>{esc(section)}</span><ul>{"".join(items)}</ul></li>'
        )

    toc = "".join(
        f'<li class="toc-h{h.level}"><a href="#{h.id}">{esc(h.text)}</a></li>'
        for h in page.headings
    )
    toc_block = (
        f'<aside class="toc" aria-label="On this page"><p>On this page</p><ul>{toc}</ul></aside>'
        if toc
        else ""
    )

    pager = []
    if prev_page:
        title = esc(prev_page.title)
        pager.append(f'<a class="prev" href="{url(prev_page.path)}">&larr; {title}</a>')
    if next_page:
        title = esc(next_page.title)
        pager.append(f'<a class="next" href="{url(next_page.path)}">{title} &rarr;</a>')

    crumbs = f'<a href="{url("/")}">Docs</a> / <span>{esc(page.section)}</span>'
    if page.path != "/":
        crumbs += f" / <span>{esc(page.title)}</span>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(page.title)} - {SITE_NAME}</title>
<meta name="description" content="{esc(page.description)}">
<link rel="stylesheet" href="{url("/static/style.css")}">
</head>
<body>
<header class="site-header">
  <a class="brand" href="{url("/")}">{SITE_NAME}</a>
  <nav class="top-links" aria-label="Site">
    <a href="{url("/getting-started/quickstart/")}">Quickstart</a>
    <a href="{url("/api/authentication/")}">API reference</a>
    <a href="{url("/help/faq/")}">FAQ</a>
    <a href="{url("/help/changelog/")}">Changelog</a>
  </nav>
</header>
<div class="layout">
  <nav class="sidebar" aria-label="Documentation"><ul>{"".join(nav)}</ul></nav>
  <main>
    <nav class="breadcrumbs" aria-label="Breadcrumb">{crumbs}</nav>
    <article>
<h1>{esc(page.title)}</h1>
{body.strip()}
    </article>
    <nav class="pager" aria-label="Previous and next">{"".join(pager)}</nav>
  </main>
  {toc_block}
</div>
<footer class="site-footer">
  <p>Larkspur is a fictional product. These docs are test data for the ask-and-do project.</p>
  <p><a href="{url("/")}">Docs home</a> &middot;
     <a href="{url("/help/changelog/")}">Changelog</a></p>
</footer>
</body>
</html>
"""


def render_sitemap(pages: list[Page], base_url: str) -> str:
    root = base_url.rstrip("/")
    urls = "".join(f"  <url><loc>{html.escape(root + p.path)}</loc></url>\n" for p in pages)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Larkspur demo docs site.")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help="where the site will be served; used in sitemap.xml and links",
    )  # fmt: skip
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    pages = build(args.out, args.base_url)
    print(f"built {len(pages)} pages into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
