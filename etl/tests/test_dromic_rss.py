"""RSS fetcher: feed parsing on a saved copy of the live feed, and the fetch
loop against a fake HTTP session (no network). Marker `network` runs one
real dry-run poll."""

import json

import pytest

from fetch import dromic_rss as rss

FEED_FILE_PREFIX = "https://dromic.dswd.gov.ph/wp-content/uploads/"


# ----------------------------------------------------------------------------
# Parsing (fixture: feed saved 22 Nov 2026)
# ----------------------------------------------------------------------------

def test_parse_feed_items(feed_xml):
    items = rss.parse_feed(feed_xml)
    assert len(items) == 10                       # WordPress default page size
    assert all(i.files for i in items)            # every post links a report
    assert all(i.pub_date.tzinfo is not None for i in items)
    assert all("Situation Reports" in i.categories for i in items)


def test_file_links_deduplicated_newest_first(feed_xml):
    tabon = next(i for i in rss.parse_feed(feed_xml) if "Tabon" in i.title)
    assert len(tabon.files) == len(set(tabon.files)) == 2
    assert "Terminal-Report" in tabon.files[0]    # newest (Nov) before Report 1 (Sep)
    assert "Report-1-on" in tabon.files[1]


@pytest.mark.parametrize("url, expected", [
    ("x/DSWD-DROMIC-Report-111-on-the-Mayon-Volcano-as-of-17-November-2026-6PM.pdf", (111, False, "17-November-2026-6PM")),
    ("x/DSWD-DROMIC-Terminal-Report-on-the-Fire-Incident-in-Brgy.-Tabon-22-November-2026-6PM.pdf", (None, True, "22-November-2026-6PM")),
    ("x/DSWD-DROMIC-Report-on-the-Fire-Incident-in-Brgy.-Umilag-Lamut-Ifugao-21-November-2026-6PM.pdf", (None, False, "21-November-2026-6PM")),
])
def test_report_info(url, expected):
    assert rss.report_info(url) == expected


def test_title_guard_catches_wrong_event_file():
    tandag = "Fire Incident in Brgy. Bag-ong Lungsod, Tandag City, Surigao del Sur [01 Nov 2026]"
    assert not rss.title_matches_file(tandag, "x/DSWD-DROMIC-Report-1-on-the-Fire-Incident-in-Brgy.-Libertad-Butuan-City.pdf")
    assert rss.title_matches_file(tandag, "x/DSWD-DROMIC-Report-1-on-the-Fire-Incident-in-Brgy.-Bag-ong-Lungsod-Tandag-City.pdf")


def test_title_guard_passes_every_item_of_saved_feed(feed_xml):
    for item in rss.parse_feed(feed_xml):
        assert rss.title_matches_file(item.title, item.files[0]), item.title


def test_google_viewer_link_rewritten():
    url = "https://docs.google.com/viewer?url=https%3A%2F%2Fdromic.dswd.gov.ph%2Fwp-content%2Fuploads%2Fa.pdf"
    assert rss.make_direct_download_link(url) == "https://dromic.dswd.gov.ph/wp-content/uploads/a.pdf"


# ----------------------------------------------------------------------------
# Fetch loop with a fake session
# ----------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves the saved feed; every report URL returns a tiny valid PDF."""

    def __init__(self, feed, etag='"v1"', fail_urls=()):
        self.feed, self.etag, self.fail_urls = feed, etag, set(fail_urls)
        self.headers, self.calls = {}, []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append((url, dict(headers or {})))
        if "/feed/" in url:
            if "paged=" in url:
                return FakeResponse(404)
            if (headers or {}).get("If-None-Match") == self.etag:
                return FakeResponse(304)
            return FakeResponse(200, self.feed, {"ETag": self.etag, "Last-Modified": "Sun, 22 Nov 2026 10:55:29 GMT"})
        if url in self.fail_urls:
            return FakeResponse(500)
        return FakeResponse(200, b"%PDF-1.7 test")


@pytest.fixture
def fake(monkeypatch, feed_xml):
    session = FakeSession(feed_xml)
    monkeypatch.setattr(rss, "_session", lambda: session)
    return session


def test_first_fetch_downloads_newest_file_per_post(tmp_path, fake):
    res = rss.fetch(2026, raw_root=tmp_path)
    assert len(res.new_files) == 10 and not res.failures
    manifest = json.loads((tmp_path / "2026" / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 10
    assert set(manifest["entries"][0]) == {"filename", "download_url", "downloaded_at", "post_url", "page"}
    state = json.loads((tmp_path / "2026" / "rss_state.json").read_text(encoding="utf-8"))
    assert state["etag"] == '"v1"' and len(state["files"]) == 10


def test_second_fetch_is_conditional_and_downloads_nothing(tmp_path, fake):
    rss.fetch(2026, raw_root=tmp_path)
    res = rss.fetch(2026, raw_root=tmp_path)
    assert res.not_modified and res.new_files == []
    assert fake.calls[-1][1].get("If-None-Match") == '"v1"'


def test_changed_feed_with_known_files_downloads_nothing(tmp_path, fake):
    rss.fetch(2026, raw_root=tmp_path)
    fake.etag = '"v2"'                                  # feed rebuilt, same posts
    res = rss.fetch(2026, raw_root=tmp_path)
    assert not res.not_modified and res.new_files == []


def test_all_reports_option_gets_older_files_too(tmp_path, fake):
    res = rss.fetch(2026, raw_root=tmp_path, all_reports=True)
    assert len(res.new_files) == 15                     # 5 posts carry an older Report 1


def test_failed_download_is_retried_next_poll(tmp_path, fake, feed_xml):
    first = rss.parse_feed(feed_xml)[0].files[0]
    fake.fail_urls = {first}
    res = rss.fetch(2026, raw_root=tmp_path)
    assert res.failures == [first] and len(res.new_files) == 9
    state = json.loads((tmp_path / "2026" / "rss_state.json").read_text(encoding="utf-8"))
    assert state["etag"] is None                        # not advanced: next poll is a full GET
    fake.fail_urls = set()
    res = rss.fetch(2026, raw_root=tmp_path)
    assert [p.name for p in res.new_files] == [first.rsplit("/", 1)[-1]]


def test_non_pdf_body_rejected(tmp_path, monkeypatch, feed_xml):
    class HtmlSession(FakeSession):
        def get(self, url, **kw):
            if "/feed/" in url:
                return super().get(url, **kw)
            return FakeResponse(200, b"<html>login</html>")
    monkeypatch.setattr(rss, "_session", lambda: HtmlSession(feed_xml))
    res = rss.fetch(2026, raw_root=tmp_path)
    assert len(res.failures) == 10 and res.new_files == []
    assert not list((tmp_path / "2026").glob("*.pdf"))


def test_dry_run_writes_nothing(tmp_path, fake):
    res = rss.fetch(2026, raw_root=tmp_path, dry_run=True)
    assert len(res.planned) == 10
    assert not (tmp_path / "2026").exists()


@pytest.mark.network
def test_live_feed_dry_run(tmp_path):
    res = rss.fetch(2026, raw_root=tmp_path, dry_run=True)
    assert res.items_seen > 0
    assert all(m.download_url.startswith(("https://", "http://")) for m in res.planned)
