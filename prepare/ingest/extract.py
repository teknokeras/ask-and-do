"""Keeping only the article body of a page (no sidebar, header, footer or
"on this page" lists), as Markdown, plus a check that flags pages where
extraction probably cut real content.

Why this matters: if navigation text ends up in the chunks, every search
result contains "Home · Docs · Pricing" and retrieval gets worse. Markdown is
kept (instead of plain text) because the chunker splits on headings and must
not break code blocks or tables.
"""

from dataclasses import dataclass
from html.parser import HTMLParser

import trafilatura

# Extracted text under 30% of the visible text gets flagged. Calibration: real
# HTTPX docs pages measured 52-96%; a nav-only page measured 21%.
LOW_COVERAGE = 0.3


@dataclass
class ExtractedPage:
    url: str
    title: str
    markdown: str
    extracted_chars: int  # letters and digits in the extracted Markdown
    visible_chars: int  # letters and digits in all visible text, navigation included

    @property
    def coverage(self) -> float:
        """Share of the page's visible text that extraction kept (0.0 to 1.0)."""
        return self.extracted_chars / self.visible_chars if self.visible_chars else 0.0

    @property
    def low_coverage(self) -> bool:
        """True when so little was kept that the page needs a human look:
        either it really is thin (an index page) or extraction went wrong."""
        return self.coverage < LOW_COVERAGE


def extract(html: str, url: str) -> ExtractedPage | None:
    """None when no article body could be found."""
    # trafilatura finds the main content block and drops page chrome.
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_formatting=True,  # keep headings, bold, inline code
        include_tables=True,  # docs often put limits and options in tables
        include_links=False,  # link URLs add noise to chunks; citations use the page URL
        include_images=False,
        include_comments=False,  # user comment sections are not documentation
        favor_recall=True,  # on doubt keep more text: losing real content is worse
    )
    if not markdown:
        return None
    metadata = trafilatura.extract_metadata(html, default_url=url)
    title = (metadata.title if metadata else None) or url
    return ExtractedPage(
        url=url,
        title=title,
        markdown=markdown,
        extracted_chars=len(_letters(markdown)),
        visible_chars=len(_letters(visible_text(html))),
    )


def visible_text(html: str) -> str:
    """All text a reader could see, navigation included. trafilatura.html2txt
    is not used here because it already drops sidebars and footers."""
    parser = _TextParser()
    parser.feed(html)
    return " ".join(parser.parts)


def _letters(text: str) -> str:
    """Text without whitespace or Markdown markup, so both sides compare fairly."""
    return "".join(ch for ch in text if ch.isalnum())


class _TextParser(HTMLParser):
    """Collects text nodes, skipping elements a reader never sees."""

    HIDDEN = {"script", "style", "head", "template", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        # How many hidden elements we are inside; text counts only at depth 0.
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.HIDDEN:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.HIDDEN and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)
