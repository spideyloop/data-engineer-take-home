"""Optional JavaScript rendering for HTML sources with missing content."""

from collections.abc import Callable

import httpx

from .http import USER_AGENT, FetchError


def render_html(
    initial: httpx.Response,
    selector: str,
    get: Callable[[str], httpx.Response],
) -> str:
    try:
        from playwright.sync_api import Error, Route, sync_playwright
    except ImportError as error:
        raise FetchError(
            "JavaScript rendering requires the browser extra and Chromium"
        ) from error

    origin = initial.url.copy_with(path="/", query=None, fragment=None)
    remaining = 6

    def route_request(route: Route) -> None:
        nonlocal remaining
        request = route.request
        url = httpx.URL(request.url)
        if (
            request.method != "GET"
            or url.copy_with(path="/", query=None, fragment=None) != origin
        ):
            route.abort()
            return
        if request.resource_type == "document" and url == initial.url:
            response = initial
        elif request.resource_type in {"script", "xhr", "fetch"} and remaining:
            remaining -= 1
            try:
                response = get(request.url)
            except (FetchError, httpx.HTTPError):
                route.abort()
                return
        else:
            route.abort()
            return
        # HTTPX supplies decoded bytes. Content-Encoding/Length belong to the original payload.
        headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower()
            not in {
                "content-encoding",
                "content-length",
                "set-cookie",
                "transfer-encoding",
            }
        }
        route.fulfill(
            status=response.status_code, headers=headers, body=response.content
        )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT, service_workers="block"
                )
                context.route("**/*", route_request)
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                page.goto(
                    str(initial.url), wait_until="domcontentloaded", timeout=75_000
                )
                page.locator(selector).first.wait_for(state="attached", timeout=75_000)
                return page.content()
            finally:
                browser.close()
    except Error as error:
        raise FetchError(
            f"JavaScript rendering failed for {initial.url}: {error}"
        ) from error
