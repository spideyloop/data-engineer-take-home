"""Normalize transport formats into source postings before extraction."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

HN = "https://news.ycombinator.com/"
BOARDS = {
    "greenhouse": ("stripe", "airbnb", "databricks", "figma"),
    "lever": ("palantir", "plaid"),
}
COMPANIES = {
    "airbnb": "Airbnb",
    "databricks": "Databricks",
    "figma": "Figma",
    "stripe": "Stripe",
    "palantir": "Palantir",
    "plaid": "Plaid",
    "spotify": "Spotify",
}
# Plaid’s Lever endpoint returned 404. Keep the replacement fixed across refreshes.
REPLACEMENTS = {("lever", "plaid"): "spotify"}
Get = Callable[[str], httpx.Response]
Render = Callable[[httpx.Response, str], str]


@dataclass(frozen=True)
class Posting:
    source: str
    listing_id: str
    url: str
    text: str
    title: str = ""
    requisition: str | None = None

    @property
    def is_ats(self) -> bool:
        return not self.source.startswith("hn:")


@dataclass(frozen=True)
class Snapshot:
    source: str
    postings: list[Posting]


def _html_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.select("script, style"):
        tag.decompose()
    return " ".join(soup.stripped_strings)


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise TypeError("expected an object in ATS response")
    return value


def _ats_posting(kind: str, board: str, row: dict) -> Posting:
    greenhouse = kind == "greenhouse"
    title = row["title"] if greenhouse else row["text"]
    url = row["absolute_url"] if greenhouse else row["hostedUrl"]
    if (
        not isinstance(title, str)
        or not title.strip()
        or urlsplit(url).scheme not in {"https", "http"}
    ):
        raise ValueError("missing title or invalid posting URL")
    if greenhouse:
        description = _html_text(_html_text(row.get("content") or ""))
        details = {"Location": _object(row.get("location") or {}).get("name")}
    else:
        description = row.get("descriptionPlain") or _html_text(
            row.get("description") or ""
        )
        details = _object(row.get("categories") or {}).copy()
        # Lever can carry useful information in lists and separate salary sections.
        for section in row.get("lists") or []:
            description += (
                "\n" + section["text"] + ": " + _html_text(section["content"])
            )
        description += "\n" + (row.get("additionalPlain") or "")
        if salary := row.get("salaryRange"):
            details["Salary"] = " ".join(
                str(_object(salary).get(key, ""))
                for key in ("currency", "min", "max", "interval")
            )
        if salary_description := row.get("salaryDescriptionPlain"):
            details["Salary details"] = salary_description
        details["Workplace type"] = row.get("workplaceType")
    company = COMPANIES.get(board, board)
    heading = [f"Company: {company}", f"Title: {title}"]
    heading.extend(f"{key}: {value}" for key, value in details.items() if value)
    return Posting(
        f"{kind}:{board}",
        str(row["id"]),
        url,
        "\n".join([*heading, description]).strip(),
        title,
        row.get("requisition_id") if greenhouse else None,
    )


def fetch_ats(get: Get, kind: str, board: str) -> Snapshot:
    if kind == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    else:
        url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    response = get(url)
    if "application/json" not in response.headers.get("content-type", "").lower():
        raise ValueError(f"expected JSON from {url}")
    payload = response.json()
    rows = _object(payload)["jobs"] if kind == "greenhouse" else payload
    if not isinstance(rows, list):
        raise TypeError(f"expected a list of jobs from {url}")
    if kind == "greenhouse":
        reported = _object(payload.get("meta") or {}).get("total")
        if reported is not None and reported != len(rows):
            raise ValueError(
                f"incomplete Greenhouse snapshot from {url}: expected {reported}, got {len(rows)}"
            )
    postings = [_ats_posting(kind, board, _object(row)) for row in rows]
    if len({posting.listing_id for posting in postings}) != len(postings):
        raise ValueError(f"duplicate listing IDs from {url}")
    return Snapshot(f"{kind}:{board}", postings)


def _hn_url(href: str) -> str:
    url = urljoin(HN, href)
    if not url.startswith(HN + "item?"):
        raise ValueError("unexpected Hacker News thread link")
    return url


def _hn_page(get: Get, url: str, selector: str, render: Render | None) -> BeautifulSoup:
    response = get(url)
    if "text/html" not in response.headers.get("content-type", "").lower():
        raise ValueError(f"expected HTML from {url}")
    page = BeautifulSoup(response.text, "html.parser")
    if render and not page.select_one(selector):
        page = BeautifulSoup(render(response, selector), "html.parser")
    return page


def fetch_hn(get: Get, today: date, *, render: Render | None = None) -> Snapshot:
    index = _hn_page(get, HN + "submitted?id=whoishiring", ".titleline > a", render)
    month = today.strftime("%B %Y")
    title = f"Ask HN: Who is hiring? ({month})"
    link = next(
        (a for a in index.select(".titleline > a") if a.get_text(strip=True) == title),
        None,
    )
    if link is None:
        raise ValueError(f"current HN hiring thread not found ({month})")
    thread_url = _hn_url(link["href"])
    source = "hn:" + thread_url.split("id=", 1)[1]
    url = thread_url
    visited = set()
    comments: dict[str, list[str]] = {}
    root_id = root_author = None

    while url:
        if url in visited or len(visited) >= 50:
            raise ValueError("unexpected HN pagination")
        visited.add(url)
        page = _hn_page(get, url, ".comment-tree", render)
        tree = page.select_one(".comment-tree")
        if tree is None:
            raise ValueError(f"missing HN comment tree at {url}")
        for row in tree.select("tr.comtr"):
            indent = row.select_one(".ind")
            author = row.select_one(".comhead a[href^='user?id='], a.hnuser")
            content = row.select_one(".commtext")
            if indent is None or "indent" not in indent.attrs:
                raise ValueError("missing HN comment indentation")
            if indent["indent"] == "0":
                root_id = row["id"]
                root_author = author.get_text(strip=True) if author else None
                if content and root_author:
                    posted = row.select_one(".age[title]")
                    timestamp = _hn_date(posted["title"]) if posted else None
                    links = [
                        a["href"]
                        for a in content.select("a[href]")
                        if urlsplit(a["href"]).scheme in {"http", "https"}
                    ]
                    parts = [content.get_text(" ", strip=True), *links]
                    if timestamp:
                        parts.append(f"Comment posted date: {timestamp}")
                    comments.setdefault(root_id, parts)
            elif (
                root_id in comments
                and author
                and content
                and author.get_text(strip=True) == root_author
            ):
                reply = "Employer follow-up: " + content.get_text(" ", strip=True)
                if reply not in comments[root_id]:
                    comments[root_id].append(reply)
        more = page.select_one("a.morelink[href]")
        url = _hn_url(more["href"]) if more else None

    postings = [
        Posting(source, key, HN + "item?id=" + key, "\n".join(parts))
        for key, parts in comments.items()
    ]
    return Snapshot(source, postings)


def _hn_date(value: str) -> str | None:
    try:
        timestamp = datetime.fromisoformat(value.split()[0])
    except (ValueError, IndexError):
        return None
    if timestamp.tzinfo:
        timestamp = timestamp.astimezone(UTC)
    return timestamp.date().isoformat()


def collect(
    get: Get, today: date, warn: Callable[[str], None], *, render: Render | None = None
) -> Iterator[Snapshot]:
    """Yield complete source snapshots. A failed source yields no snapshot."""
    from .http import FetchError

    failures = (
        FetchError,
        httpx.HTTPError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    )
    for kind, boards in BOARDS.items():
        for original in boards:
            board = REPLACEMENTS.get((kind, original), original)
            if board != original:
                warn(f"Using {kind}:{board} in place of unavailable {kind}:{original}")
            try:
                snapshot = fetch_ats(get, kind, board)
            except failures as error:
                warn(f"{kind}:{board}: {error}")
                continue
            if not snapshot.postings:
                warn(
                    f"{kind}:{board}: empty board, check whether a replacement is needed"
                )
            yield snapshot

    try:
        yield fetch_hn(get, today, render=render)
    except failures as error:
        warn(f"Hacker News: {error}")
