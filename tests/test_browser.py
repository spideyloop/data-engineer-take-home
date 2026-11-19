from pathlib import Path

import httpx
import pytest

from pipeline.browser import render_html
from pipeline.http import ScrapeClient
from pipeline.storage import Store


def test_javascript_uses_robots_aware_fetches_and_blocks_other_origins(tmp_path):
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium is not installed")

    requests = []

    def respond(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        if request.url.path == "/jobs":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<script src="/jobs.js"></script><script src="https://other.test/extra.js"></script>',
            )
        if request.url.path == "/jobs.js":
            return httpx.Response(
                200,
                headers={"content-type": "application/javascript"},
                text="""
                fetch('/private').catch(() => {});
                fetch('/api/jobs').then(r => r.text()).then(text => {
                    document.body.innerHTML = '<main id="jobs">' + text + '</main>';
                });
                """,
            )
        assert request.url.path == "/api/jobs"
        return httpx.Response(200, text="Data Engineer")

    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
    ):
        scraper = ScrapeClient(client, store, sleep=lambda _: None)
        initial = scraper.get("https://example.test/jobs")
        rendered = render_html(
            initial, "#jobs", lambda url: scraper.get(url, same_origin=True)
        )
    assert '<main id="jobs">Data Engineer</main>' in rendered
    assert requests == [
        "https://example.test/robots.txt",
        "https://example.test/jobs",
        "https://example.test/jobs.js",
        "https://example.test/api/jobs",
    ]
