from datetime import date

import httpx
import pytest

from pipeline.sources import fetch_ats, fetch_hn


def test_lever_html_cannot_be_mistaken_for_empty_board():
    def get(url):
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html/>"
        )

    with pytest.raises(ValueError, match="expected JSON"):
        fetch_ats(get, "lever", "plaid")


def test_hn_follows_more_and_only_attaches_employer_replies():
    base = "https://news.ycombinator.com/"
    index = '<span class="titleline"><a href="item?id=1">Ask HN: Who is hiring? (November 2026)</a></span>'
    opening = """
    <tr class="comtr" id="10"><td class="ind" indent="0"></td><td>
      <span class="comhead"><a href="user?id=employer">employer</a></span><div class="commtext">Example | Data Engineer | US</div>
    </td></tr>
    <tr class="comtr" id="11"><td class="ind" indent="1"></td><td>
      <span class="comhead"><a href="user?id=someone">someone</a></span><div class="commtext">Did this close?</div>
    </td></tr>
    <tr class="comtr" id="12"><td class="ind" indent="2"></td><td>
      <span class="comhead"><a href="user?id=employer">employer</a></span><div class="commtext">We filled the role.</div>
    </td></tr>"""
    next_page = """
    <tr class="comtr" id="20"><td class="ind" indent="0"></td><td>
      <span class="comhead"><a href="user?id=hiring">hiring</a></span><div class="commtext">Second | ML Engineer | Remote US</div>
    </td></tr>"""
    pages = {
        base + "submitted?id=whoishiring": index,
        base + "item?id=1": '<table class="comment-tree">'
        + opening
        + '</table><a class="morelink" href="item?id=1&p=2">More</a>',
        base + "item?id=1&p=2": '<table class="comment-tree">' + next_page + "</table>",
    }

    def get(url):
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text=pages[url]
        )

    snapshot = fetch_hn(get, date(2026, 11, 17))
    assert [posting.listing_id for posting in snapshot.postings] == ["10", "20"]
    assert snapshot.postings[0].url == base + "item?id=10"
    assert "We filled the role." in snapshot.postings[0].text
    assert "Did this close?" not in snapshot.postings[0].text


def test_incomplete_greenhouse_response_cannot_withdraw_absent_listings():
    response = httpx.Response(200, json={"jobs": [], "meta": {"total": 4}})
    with pytest.raises(ValueError, match="incomplete"):
        fetch_ats(lambda url: response, "greenhouse", "stripe")


def test_hn_uses_renderer_when_static_html_lacks_the_comment_tree():
    base = "https://news.ycombinator.com/"
    pages = {
        base + "submitted?id=whoishiring": (
            '<span class="titleline"><a href="item?id=1">Ask HN: Who is hiring? (November 2026)</a></span>'
        ),
        base + "item?id=1": '<script src="/jobs.js"></script>',
    }

    def get(url):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=pages[url],
            request=httpx.Request("GET", url),
        )

    def render(response, selector):
        assert str(response.url) == base + "item?id=1"
        assert selector == ".comment-tree"
        return (
            '<table class="comment-tree"><tr class="comtr" id="42"><td class="ind" indent="0"></td>'
            '<td><a class="hnuser">employer</a><div class="commtext">Data Engineer | US</div></td></tr></table>'
        )

    snapshot = fetch_hn(get, date(2026, 11, 17), render=render)
    assert [posting.listing_id for posting in snapshot.postings] == ["42"]
