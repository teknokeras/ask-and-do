import httpx

from prepare.ingest.fetcher import USER_AGENT, HttpFetcher


def make_fetcher(handler, **kwargs):
    sleeps: list[float] = []
    fetcher = HttpFetcher(transport=httpx.MockTransport(handler), sleep=sleeps.append, **kwargs)
    return fetcher, sleeps


def test_returns_html_and_sends_user_agent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, html="<p>hi</p>")

    fetcher, _ = make_fetcher(handler)
    result = fetcher.fetch("https://docs.example.com/")
    assert result.ok and result.is_html and result.text == "<p>hi</p>"
    assert seen["ua"] == USER_AGENT


def test_retries_5xx_with_backoff_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, text="ok")

    fetcher, sleeps = make_fetcher(handler, backoff=1.0)
    assert fetcher.fetch("https://docs.example.com/").status == 200
    assert sleeps == [1.0, 2.0]


def test_honors_retry_after_on_429():
    responses = [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200)]
    fetcher, sleeps = make_fetcher(lambda request: responses.pop(0))
    assert fetcher.fetch("https://docs.example.com/").status == 200
    assert sleeps == [7.0]


def test_does_not_retry_404():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    fetcher, sleeps = make_fetcher(handler)
    assert fetcher.fetch("https://docs.example.com/missing").status == 404
    assert len(calls) == 1 and sleeps == []


def test_network_error_after_retries_is_reported_not_raised():
    def handler(request):
        raise httpx.ConnectError("refused")

    fetcher, sleeps = make_fetcher(handler, retries=2)
    result = fetcher.fetch("https://docs.example.com/")
    assert result.status == 0 and result.error == "network error: ConnectError"
    assert len(sleeps) == 2


def test_follows_redirect_and_reports_final_url():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://docs.example.com/new/"})
        return httpx.Response(200, html="new")

    fetcher, _ = make_fetcher(handler)
    result = fetcher.fetch("https://docs.example.com/old")
    assert result.final_url == "https://docs.example.com/new/"


def test_rejects_oversized_bodies():
    fetcher, _ = make_fetcher(lambda request: httpx.Response(200, text="x" * 100), max_bytes=10)
    result = fetcher.fetch("https://docs.example.com/")
    assert not result.ok and result.error == "too large"
