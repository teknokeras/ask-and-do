from prepare.ingest.chunk import MAX_WORDS, chunk_page, parse_blocks
from prepare.ingest.extract import ExtractedPage, extract, heading_ids, heading_key

URL = "https://docs.example.com/docs/clients/"


def page(markdown: str, heading_ids=()) -> ExtractedPage:
    return ExtractedPage(URL, "Clients", markdown, 0, 0, list(heading_ids))


def words(n: int, word: str = "word") -> str:
    return " ".join([word] * n)


def test_hash_lines_inside_code_are_not_headings():
    md = "# Proxies\n\n```\n# This request goes through the proxy\nhttpx.get(url)\n```\n"
    kinds = [b.kind for b in parse_blocks(md)]
    assert kinds == ["heading", "code"]


def test_heading_link_symbols_are_dropped_from_the_body():
    md = f"# Clients\n\n## Timeouts#\n\n{words(300)}\n\n## Proxies ¶\n\n{words(300)}\n"
    chunks = chunk_page(page(md))
    body = "\n".join(c.body for c in chunks)
    assert "## Timeouts\n" in body and "## Proxies\n" in body
    assert "#\n" not in body and "¶" not in body
    assert "Clients > Proxies" in [c.breadcrumb for c in chunks]


def test_code_blocks_and_tables_stay_whole():
    md = "\n".join(
        [
            "# Big",
            "```",
            *[f"line_{i} = {i}" for i in range(400)],
            "```",
            "| a | b |",
            "|---|---|",
            *[f"| row {i} | {words(5)} |" for i in range(120)],
        ]
    )
    chunks = chunk_page(page(md))
    code = [c for c in chunks if "line_0 = 0" in c.body]
    assert len(code) == 1 and "line_399 = 399" in code[0].body
    table = [c for c in chunks if "| row 0 |" in c.body]
    assert len(table) == 1 and "| row 119 |" in table[0].body


def test_breadcrumbs_follow_heading_levels():
    md = "\n\n".join(
        [
            "# Clients",
            words(60),
            "## Usage",
            words(60),
            "### Async",
            words(60),
            "## Limits",
            words(60),
        ]
    )
    crumbs = [c.breadcrumb for c in chunk_page(page(md))]
    assert crumbs == [
        "Clients",
        "Clients > Usage",
        "Clients > Usage > Async",
        "Clients > Limits",
    ]


def test_small_child_merges_into_parent_but_siblings_do_not():
    md = "\n\n".join(
        ["# Clients", words(80), "## Tiny", words(5), "## Other", words(80), "## Tiny 2", words(5)]
    )
    chunks = chunk_page(page(md))
    # "Tiny" sits under "Clients", so it merges into it. "Tiny 2" sits next to
    # "Other", not under it, so it stays its own chunk even though it is small.
    assert [c.breadcrumb for c in chunks] == ["Clients", "Clients > Other", "Clients > Tiny 2"]
    assert "## Tiny" in chunks[0].body


def test_large_sections_are_split_with_overlap():
    paragraphs = [f"para{i} " + words(29) for i in range(30)]  # 30 words each, 900 total
    chunks = chunk_page(page("# Clients\n\n" + "\n\n".join(paragraphs)))
    assert len(chunks) > 1
    assert all(len(c.body.split()) <= MAX_WORDS for c in chunks)
    # Each later piece starts with the paragraph that ended the piece before.
    for before, after in zip(chunks, chunks[1:], strict=False):
        assert after.body.split("\n\n")[0] == before.body.split("\n\n")[-1]


def test_ids_are_stable_and_hashes_track_content():
    md = "\n\n".join(["# Clients", words(80), "## Usage", words(80), "## Limits", words(80)])
    first = chunk_page(page(md))
    again = chunk_page(page(md))
    assert [c.id for c in first] == [c.id for c in again]
    assert len({c.id for c in first}) == len(first)

    edited = chunk_page(page(md.replace("## Limits\n\n" + words(80), "## Limits\n\nnew text")))
    assert [c.id for c in edited] == [c.id for c in first]
    changed = [
        a.breadcrumb for a, b in zip(first, edited, strict=True) if a.chunk_hash != b.chunk_hash
    ]
    assert changed == ["Clients > Limits"]


def test_repeated_headings_get_different_ids_and_their_own_anchors():
    md = "\n\n".join(["# Clients", words(80), "## Example", words(80), "## Example", words(80)])
    ids = [(heading_key("Example"), "example"), (heading_key("Example"), "example-1")]
    chunks = chunk_page(page(md, ids))
    assert [c.anchor for c in chunks] == [None, "example", "example-1"]
    assert len({c.id for c in chunks}) == 3


def test_heading_ids_are_read_from_the_page_html(fixture_html):
    # The "¶" link symbol that doc generators append to headings is ignored.
    assert heading_ids(fixture_html("docs_page.html")) == [
        ("usingtheapi", "using-the-api"),
        ("limits", "limits"),
    ]


def test_a_short_page_becomes_one_chunk(fixture_html):
    # 115 words in total: the subsections merge into the page's top section.
    chunks = chunk_page(extract(fixture_html("docs_page.html"), URL))
    assert len(chunks) == 1
    assert "## Using the API" in chunks[0].body and "Members per group" in chunks[0].body
