"""Polite HTTP for source collection."""

import logging
import time
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from .storage import Store

USER_AGENT = "JobPostingsTakeHome/1.0"
LOG = logging.getLogger(__name__)
RETRYABLE = {429, 500, 502, 503, 504}
REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(Exception):
    pass


class ScrapeClient:
    def __init__(
        self,
        client: httpx.Client,
        store: Store,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.client = client
        self.store = store
        self.clock = clock
        self.sleep = sleep
        self.last_request: dict[str, float] = {}
        self.delays: dict[str, float] = {}
        self.rules: dict[str, RobotFileParser] = {}

    def _request(self, url: str) -> httpx.Response:
        host = httpx.URL(url).host
        for attempt in range(3):
            previous = self.last_request.get(host)
            if previous is not None:
                remaining = previous + self.delays.get(host, 1.0) - self.clock()
                if remaining > 0:
                    self.sleep(remaining)
            self.last_request[host] = self.clock()
            try:
                response = self.client.get(
                    url, headers={"User-Agent": USER_AGENT}, follow_redirects=False
                )
            except httpx.TransportError:
                if attempt == 2:
                    raise
                retry_after = ""
            else:
                self.store.save_response(response)
                if response.status_code not in RETRYABLE or attempt == 2:
                    return response
                retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdecimal() and int(retry_after) > 60:
                raise FetchError(f"server requested a retry delay over 60s for {url}")
            delay = max(2**attempt, int(retry_after) if retry_after.isdecimal() else 0)
            LOG.warning("Retrying %s in %ss", url, delay)
            self.sleep(delay)
        raise AssertionError("retry loop must return or raise")

    def _robots(self, url: str) -> RobotFileParser:
        location = httpx.URL(url)
        host = location.host
        origin = str(location.copy_with(path="/", query=None, fragment=None))
        if origin in self.rules:
            return self.rules[origin]
        robots_url = urljoin(origin, "robots.txt")
        response = self._request(robots_url)
        for _ in range(5):
            if response.status_code not in REDIRECTS:
                break
            destination = _redirect(response)
            response = self._request(destination)

        rules = RobotFileParser(robots_url)
        if response.status_code in {404, 410}:
            rules.parse([])
        elif response.status_code == 200 and not response.text.lstrip().startswith("<"):
            rules.parse(response.text.splitlines())
        else:
            raise FetchError(
                f"could not verify robots.txt at {robots_url} ({response.status_code})"
            )
        rate = rules.request_rate(USER_AGENT)
        interval = rate.seconds / rate.requests if rate and rate.requests else 0
        self.delays[host] = max(
            1, self.delays.get(host, 1), rules.crawl_delay(USER_AGENT) or 0, interval
        )
        self.rules[origin] = rules
        return rules

    def get(self, url: str, *, same_origin: bool = False) -> httpx.Response:
        origin = httpx.URL(url).copy_with(path="/", query=None, fragment=None)
        for _ in range(6):
            if (
                same_origin
                and httpx.URL(url).copy_with(path="/", query=None, fragment=None)
                != origin
            ):
                raise FetchError(f"cross-origin redirect blocked for {url}")
            if not self._robots(url).can_fetch(USER_AGENT, url):
                raise FetchError(f"robots.txt disallows {url}")
            response = self._request(url)
            if response.status_code not in REDIRECTS:
                return response.raise_for_status()
            url = _redirect(response)
        raise FetchError("too many redirects")


def _redirect(response: httpx.Response) -> str:
    destination = response.headers.get("Location")
    url = urljoin(str(response.url), destination) if destination else ""
    if httpx.URL(url).scheme not in {"http", "https"}:
        raise FetchError(f"invalid redirect from {response.url}")
    return url
