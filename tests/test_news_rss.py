"""Strict bounded RSS/Atom, no entity execution, date guessing, silent partial or HTML."""

from datetime import timedelta

import pytest

from core.settings import Settings
from news.rss_parser import parse_rss
from news.types import NewsInvalid, article_url, plain_text
from scripts.synthetic_news_fixtures import RSS_URL, rss_document
from scripts.synthetic_signal_market import ANCHOR


def parse(raw, **changes):
    return parse_rss(raw, url=RSS_URL, settings=Settings(_env_file=None, **changes), observed_at=ANCHOR)


def test_valid_rss_sanitizes_content_and_keeps_publication_not_poll_time():
    rows = parse(
        rss_document(ANCHOR, title="<b>Euro rallies</b>", summary="<script>bad()</script> Good & strong")
    )
    assert rows[0].title == "Euro rallies" and "bad()" not in rows[0].summary
    assert rows[0].published_at == ANCHOR - timedelta(minutes=5) and rows[0].first_seen_at == ANCHOR


def test_atom_publication_offset_is_explicit_and_update_only_is_not_publication():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>ATOM</title><id>x</id>
    <entry><id>story1</id><title>Gold market</title><link href="https://article.example/story"/>
    <published>2026-10-03T18:55:00+07:00</published><updated>2026-10-03T11:59:00Z</updated>
    <summary>Neutral</summary></entry></feed>"""
    assert parse(body)[0].published_at == ANCHOR - timedelta(minutes=5)
    body = body.replace(b"<published>2026-10-03T18:55:00+07:00</published>", b"")
    with pytest.raises(NewsInvalid):
        parse(body)


@pytest.mark.parametrize(
    "raw",
    [
        b"not XML",
        b"<rss><channel><item>",
        b"<html><body>Rate limited</body></html>",
        b'<!DOCTYPE rss [<!ENTITY secret SYSTEM "file:///etc/passwd">]><rss><channel><title>&secret;</title></channel></rss>',
        b'<!DOCTYPE rss [<!ENTITY x "x">]><rss><channel/></rss>',
        rss_document(ANCHOR).replace(b"<pubDate>Sat, 03 Oct 2026 11:55:00 +0000</pubDate>", b""),
        rss_document(ANCHOR).replace(b"+0000", b""),
        rss_document(ANCHOR, published_at=ANCHOR + timedelta(seconds=1)),
        rss_document(ANCHOR, url="javascript:alert(1)"),
        rss_document(ANCHOR).replace(
            b"<title>Synthetic Euro and dollar market commentary</title>", b"<title></title>"
        ),
    ],
)
def test_malformed_missing_naive_future_dates_and_unsafe_links_reject_whole_source(raw):
    with pytest.raises(NewsInvalid):
        parse(raw)


def test_missing_timestamp_in_one_item_does_not_silently_certify_other_items():
    raw = rss_document(ANCHOR).replace(
        b"</channel>", b"<item><title>Other</title><link>https://x.example/a</link></item></channel>"
    )
    with pytest.raises(NewsInvalid):
        parse(raw)


def test_empty_valid_feed_returns_empty_not_a_safe_assertion():
    assert parse(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>') == ()


def test_feed_bytes_depth_and_item_bounds():
    with pytest.raises(NewsInvalid):
        parse(b" " * 1048577)
    with pytest.raises(NewsInvalid):
        parse(b"<rss>" + b"<x>" * 22 + b"</x>" * 22 + b"</rss>")
    raw = rss_document(ANCHOR)
    item = raw[raw.index(b"<item>") : raw.index(b"</item>") + 7]
    with pytest.raises(NewsInvalid):
        parse(raw.replace(item, item * 11), news_max_items_per_source=10)


def test_display_links_drop_tracking_auth_fragment_but_are_never_fetched():
    cleaned = article_url("https://example.com/a?auth_token=FAKE&secret=BAD&utm_source=x&id=4#frag")
    assert cleaned == "https://example.com/a?id=4"
    assert plain_text("token=FAKESECRET", secrets=("FAKESECRET",)) == "token=[REDACTED]"
