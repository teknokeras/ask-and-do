"""Cutting an extracted page into chunks: the pieces that get embedded,
searched and cited.

Steps, for one page:
1. Parse the Markdown into blocks: headings, code blocks, tables, paragraphs.
   Code fences are tracked, so a "# comment" inside code is not a heading.
2. Group blocks into sections, one per heading, each with its heading path
   ("Clients > Making requests").
3. Merge small sections into their parent section, so a heading with one line
   under it does not become a useless chunk on its own.
4. Split big sections at block boundaries with a small overlap. A code block or
   table is never cut; one that is bigger than the limit becomes its own chunk.
5. Give each chunk a stable ID (same page structure -> same IDs on every run)
   and a content hash (changes only when the chunk's text changes).

Sizes are counted in words, roughly 1.3 tokens each, to avoid a tokenizer
dependency. They are starting values; the evals should tune them.
"""

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

from prepare.ingest.extract import ExtractedPage, heading_key

TARGET_WORDS = 230  # about 300 tokens: aim for this when splitting
MAX_WORDS = 380  # about 500 tokens: no chunk goes over this unless one block does
MIN_WORDS = 50  # sections under this get merged into their parent when possible
OVERLAP_WORDS = 40  # a paragraph this short is repeated at the start of the next split

# Group 2 is the heading text without trailing "#", "¶" or "§": the self-link
# symbols doc generators put after headings, which trafilatura keeps as text
# ("## Create vs invite#"). A heading that really ends in "#" ("C#") loses it.
HEADING = re.compile(r"^(#{1,6})\s+(.+?)[\s#¶§]*$")
FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Block:
    kind: str  # "heading", "code", "table" or "text"
    text: str
    level: int = 0  # heading level 1-6, only for headings

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Section:
    path: list[str]  # heading texts from the top, e.g. ["Clients", "Making requests"]
    level: int  # level of the heading that starts it; 0 for text before any heading
    anchor: str | None  # the heading's id on the real page, if it has one
    blocks: list[Block] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(b.words for b in self.blocks)

    def contains(self, other: "Section") -> bool:
        """True when other sits under this section's heading."""
        return other.level > self.level and other.path[: len(self.path)] == self.path


@dataclass
class Chunk:
    id: str  # stable across runs: from URL, heading path and position
    url: str
    anchor: str | None  # section id, so a citation can link to url#anchor
    title: str  # page title
    breadcrumb: str  # "Clients > Making requests", embedded together with the body
    body: str  # Markdown text of the chunk
    chunk_hash: str  # hash of breadcrumb + body, to skip re-embedding unchanged chunks
    position: int  # order of the chunk within its page

    @property
    def citation_url(self) -> str:
        return f"{self.url}#{self.anchor}" if self.anchor else self.url

    @property
    def embed_text(self) -> str:
        """What gets embedded. The breadcrumb is included so a chunk that only
        says "Set it to 30" still carries what "it" is."""
        return embed_text(self.breadcrumb, self.body)


def embed_text(breadcrumb: str, body: str) -> str:
    return f"{breadcrumb}\n{body}"


def chunk_page(page: ExtractedPage) -> list[Chunk]:
    sections = _sections(parse_blocks(page.markdown), page)
    pieces: list[tuple[Section, list[Block]]] = []
    for section in _merge_small(sections):
        pieces.extend((section, blocks) for blocks in _split(section.blocks))

    chunks = []
    # Counts chunks per heading path, so two pieces of one split section, or
    # two sections with the same headings, still get different IDs.
    seen_paths: dict[str, int] = defaultdict(int)
    for position, (section, blocks) in enumerate(pieces):
        breadcrumb = " > ".join(section.path) or page.title
        index = seen_paths[breadcrumb]
        seen_paths[breadcrumb] += 1
        body = "\n\n".join(b.text for b in blocks)
        chunks.append(
            Chunk(
                id=_hash(f"{page.url}\n{breadcrumb}\n{index}"),
                url=page.url,
                anchor=section.anchor,
                title=page.title,
                breadcrumb=breadcrumb,
                body=body,
                chunk_hash=_hash(embed_text(breadcrumb, body)),
                position=position,
            )
        )
    return chunks


def parse_blocks(markdown: str) -> list[Block]:
    """Turn Markdown into a flat list of blocks, keeping code blocks and
    tables whole."""
    blocks: list[Block] = []
    lines = markdown.splitlines()
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(Block("text", "\n".join(paragraph).strip()))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = FENCE.match(line)
        if fence:
            # Everything up to the matching closing fence is one code block,
            # even lines that look like headings or tables.
            flush_paragraph()
            marker = fence.group(1)
            code = [line]
            i += 1
            while i < len(lines):
                code.append(lines[i])
                if lines[i].strip().startswith(marker):
                    break
                i += 1
            blocks.append(Block("code", "\n".join(code)))
        elif line.lstrip().startswith("|"):
            # Consecutive "|" lines form one table.
            flush_paragraph()
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].rstrip())
                i += 1
            blocks.append(Block("table", "\n".join(rows)))
            continue
        elif heading := HEADING.match(line):
            flush_paragraph()
            # Stored cleaned, so the symbols stay out of embedded and quoted text.
            level = len(heading.group(1))
            blocks.append(Block("heading", f"{'#' * level} {heading.group(2)}", level=level))
        elif not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line)
        i += 1
    flush_paragraph()
    return [b for b in blocks if b.text]


def _sections(blocks: list[Block], page: ExtractedPage) -> list[Section]:
    """One section per heading, with the heading path built from the levels."""
    # Real heading ids from the HTML, matched by heading text. A deque per text
    # handles pages where two headings share a name ("Example", "Example").
    ids: dict[str, deque[str]] = defaultdict(deque)
    for key, heading_id in page.heading_ids:
        ids[key].append(heading_id)

    sections = [Section(path=[], level=0, anchor=None)]  # text before the first heading
    stack: list[tuple[int, str]] = []  # (level, text) of the headings we are under
    for block in blocks:
        if block.kind == "heading":
            text = HEADING.match(block.text).group(2)
            # Leaving deeper or equal headings: "## B" after "### A" pops A.
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, text))
            key = heading_key(text)
            anchor = ids[key].popleft() if ids[key] else None
            sections.append(Section([t for _, t in stack], block.level, anchor, [block]))
        else:
            sections[-1].blocks.append(block)
    return [s for s in sections if s.blocks]


def _merge_small(sections: list[Section]) -> list[Section]:
    """Merge a section into the one before it when one of them is small, the
    later one sits under the earlier one's heading, and the result fits.
    Siblings are never merged, so each chunk stays about one topic."""
    merged: list[Section] = []
    for section in sections:
        if merged:
            parent = merged[-1]
            small = parent.words < MIN_WORDS or section.words < MIN_WORDS
            if small and parent.contains(section) and parent.words + section.words <= MAX_WORDS:
                parent.blocks.extend(section.blocks)
                continue
        merged.append(Section(section.path, section.level, section.anchor, list(section.blocks)))
    return merged


def _split(blocks: list[Block]) -> list[list[Block]]:
    """Split a section's blocks into pieces of about TARGET_WORDS. Cuts only
    between blocks, and starts each new piece with the previous piece's last
    paragraph when it is short, so text near the cut keeps its context."""
    if sum(b.words for b in blocks) <= MAX_WORDS:
        return [blocks]
    pieces: list[list[Block]] = []
    current: list[Block] = []
    words = 0
    for block in blocks:
        if current and words + block.words > TARGET_WORDS:
            pieces.append(current)
            last = current[-1]
            overlap = last.kind == "text" and last.words <= OVERLAP_WORDS
            current = [last] if overlap else []
            words = last.words if overlap else 0
        current.append(block)
        words += block.words
    if current:
        pieces.append(current)
    return pieces


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
