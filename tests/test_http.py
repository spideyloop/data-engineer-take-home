import httpx
import pytest

from pipeline.http import FetchError, ScrapeClient
from pipeline.storage import Store


def test_hn_delay_applies_between_robots_and_html(tmp_path):
    visited = []
    delays = []
    now = [0.0]

    def respond(request):
        visited.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nCrawl-delay: 30")

    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds

    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
    ):
        fetch = ScrapeClient(client, store, clock=lambda: now[0], sleep=sleep)
        fetch.get("https://news.ycombinator.com/item?id=1")
        assert visited == ["/robots.txt", "/item"]
        assert delays == [30]
        assert store.db.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 2


def test_robots_denial_skips_the_page(tmp_path):
    def respond(request):
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /private")

    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(FetchError, match="robots.txt disallows"),
    ):
        ScrapeClient(client, store).get("https://example.com/private")
