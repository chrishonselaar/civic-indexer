"""The UI is not to be indexed by search engines (admin decision 2026-09-08): the demo
host carries T2 full text. Both mechanisms must hold, because crawlers differ in which
one they honour — and /bot must stay fetchable, since the User-Agent promises it."""

from fastapi.testclient import TestClient

from ui import search_api

client = TestClient(search_api.app)


def test_robots_txt_disallows_everything_by_default():
    assert search_api.ROBOTS_NOINDEX, "noindex must be the default; lifting it is opt-in"
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.text.startswith("User-agent: *\nDisallow: /")


def test_every_response_carries_noindex_header():
    for path in ("/", "/bot", "/bewijs", "/health"):
        r = client.get(path)
        assert r.headers.get("x-robots-tag") == "noindex, nofollow", path


def test_bot_page_still_fetchable():
    assert client.get("/bot").status_code == 200
