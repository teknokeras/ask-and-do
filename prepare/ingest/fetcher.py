"""Fetching behind a small interface, so tests can use saved pages and a
login-capable fetcher can be added later without changing the crawler.

The crawler only ever calls fetcher.fetch(url) and reads the FetchResult. It
never talks to httpx directly. That keeps network concerns (retries, timeouts,
redirects, size limits) in this file and lets tests pass in a fake fetcher.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

# Identifies the crawler to site owners and points them at the repo, so they can
# see what it is and block it in robots.txt if they want to.
USER_AGENT = "ask-and-do-crawler/0.1 (+https://github.com/teknokeras/ask-and-do)"

# Status codes that usually mean "try again later" rather than "this is wrong":
# rate limited (429) or a temporary server problem (5xx).
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    """Everything the crawler needs to know about one request. Failures are
    returned as values (status 0 or an error message), never raised, so a single
    bad page shows up in the report instead of stopping the crawl."""

    url: str  # the URL that was requested
    final_url: str  # after redirects
    status: int  # 0 when the request failed before a response
    content_type: str = ""
    text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and self.error is None

    @property
    def is_html(self) -> bool:
        # Content-Type can carry extras like "text/html; charset=utf-8"; compare
        # only the media type before the ";".
        return self.content_type.split(";")[0].strip() in ("text/html", "application/xhtml+xml")


class Fetcher(Protocol):
    """Anything with this method can be used by the crawler (HttpFetcher, the
    FakeFetcher in tests, or a future fetcher that logs in first)."""

    def fetch(self, url: str) -> FetchResult: ...


class HttpFetcher:
    """Plain HTTP fetcher: timeouts, retries with backoff on 429/5xx and network
    errors, a size cap, and redirects followed (the crawler checks where they land)."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        retries: int = 3,  # extra attempts after the first one
        backoff: float = 1.0,  # first wait in seconds; doubles each retry (1, 2, 4...)
        max_bytes: int = 5_000_000,  # pages bigger than this are almost never real docs
        transport: httpx.BaseTransport | None = None,  # tests pass httpx.MockTransport
        sleep: Callable[[float], None] = time.sleep,  # tests pass a recorder, so no real waits
    ) -> None:
        self.retries = retries
        self.backoff = backoff
        self.max_bytes = max_bytes
        self.sleep = sleep
        # One client for the whole crawl reuses connections to the same host.
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    def fetch(self, url: str) -> FetchResult:
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                result, retry_after = self._fetch_once(url)
            except httpx.TooManyRedirects:
                # A redirect loop will not fix itself, so no retry.
                return FetchResult(url, url, 0, error="too many redirects")
            except httpx.TransportError as exc:
                # Connection refused, DNS failure, timeout and similar: worth retrying.
                if last:
                    return FetchResult(url, url, 0, error=f"network error: {type(exc).__name__}")
                self.sleep(self.backoff * 2**attempt)
                continue
            # Success, or an error that will not change on retry (404, 403...):
            # return it as is. Same when we are out of attempts.
            if result.status not in RETRY_STATUSES or last:
                return result
            # Prefer the server's own Retry-After hint, but never wait over a minute.
            self.sleep(min(retry_after or self.backoff * 2**attempt, 60))
        raise AssertionError("unreachable")

    def _fetch_once(self, url: str) -> tuple[FetchResult, float | None]:
        # Streaming lets us stop reading as soon as the size cap is passed,
        # instead of downloading a huge file into memory first.
        with self.client.stream("GET", url) as response:
            content_type = response.headers.get("content-type", "")
            # response.url is where we ended up after following redirects.
            final_url = str(response.url)
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self.max_bytes:
                    return FetchResult(
                        url, final_url, response.status_code, content_type, error="too large"
                    ), None
            # Decode with the charset the server declared, falling back to UTF-8.
            # errors="replace" keeps a page with a few bad bytes usable.
            text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
            result = FetchResult(url, final_url, response.status_code, content_type, text)
            return result, _retry_after(response.headers.get("retry-after"))

    def close(self) -> None:
        self.client.close()


def _retry_after(value: str | None) -> float | None:
    """Seconds to wait from a Retry-After header, which is either a number of
    seconds ("120") or an HTTP date ("Wed, 21 Oct 2026 07:28:00 GMT")."""
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        return max(parsedate_to_datetime(value).timestamp() - time.time(), 0.0)
    except (TypeError, ValueError):
        return None
