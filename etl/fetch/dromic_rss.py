"""DROMIC situation-report fetcher using the WordPress category RSS feed.

Replaces the Selenium scraper (fetch/dromic.py) for routine updates; the
scraper stays as the fallback for history (notes.md §7.3, §8.16).

Why RSS:
  - The feed item's `content:encoded` holds every report file linked in the
    post (newest first), so a post that gains Report #112 is picked up again.
    The Selenium scraper keyed state by *post* URL and never revisited.
  - No browser/Chromedriver; one HTTP GET per poll, and a conditional GET
    (ETag / If-Modified-Since) makes an unchanged poll a single 304.

State:
  - manifest.json (same file and entry shape as fetch/dromic.py, which the
    parser reads for provenance). Idempotency key: the file's download_url.
  - rss_state.json next to it: feed ETag/Last-Modified, newest pubDate seen,
    and the extra per-file metadata (title, guid, categories, report number,
    as-of date). Kept separate so fetch/dromic.py's ManifestEntry(**entry)
    keeps loading the shared manifest.

The feed shows 10 posts per page. When every post on page 1 is newer than the
last poll, older pages are read too (up to --max-pages) so a burst of posts
between polls is not missed.

Run from etl/:
    python -m fetch.dromic_rss --year 2026 --dry-run
    python -m fetch.dromic_rss --year 2026
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT_DIR / "data" / "raw" / "dromic-new"
FEED_URL = "https://dromic.dswd.gov.ph/category/situation-reports/{year}/feed/"
USER_AGENT = "SakunaGraPH-ETL/1.0 (academic research; DROMIC situation reports)"
TIMEOUT = 60
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
FILE_EXTENSIONS = (".pdf", ".docx", ".doc")

_HREF = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_REPORT_NO = re.compile(r"DROMIC-(?:Terminal-)?Report-(\d+)-on", re.IGNORECASE)
_TERMINAL = re.compile(r"DROMIC-Terminal-Report", re.IGNORECASE)
_AS_OF = re.compile(r"(\d{1,2}-[A-Za-z]+-20\d{2}(?:-\d{1,2}(?:AM|PM))?)\.(?:pdf|docx?)$", re.IGNORECASE)
_STOPWORDS = {
    "incident", "fire", "brgy", "barangay", "city", "province", "effects",
    "terminated", "with", "from", "region", "the", "and", "sept", "2025", "2026",
}


# =============================================================================
# Data
# =============================================================================

@dataclass
class FeedItem:
    title: str
    link: str
    guid: str
    pub_date: datetime
    categories: list[str]
    files: list[str]  # direct file URLs, newest first, de-duplicated


@dataclass
class FileMeta:
    """Extra per-file metadata kept in rss_state.json (not in manifest.json)."""
    download_url: str
    filename: str
    post_title: str
    post_url: str
    guid: str
    pub_date: str
    categories: list[str]
    report_number: Optional[int]
    terminal: bool
    report_as_of: Optional[str]
    title_check: str  # "ok" | "mismatch" (see title_matches_file)


@dataclass
class RssState:
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    newest_pub_date: Optional[str] = None  # ISO-8601 UTC
    files: dict[str, dict] = field(default_factory=dict)  # download_url -> FileMeta


@dataclass
class FetchResult:
    year: int
    not_modified: bool = False
    items_seen: int = 0
    pages_read: int = 0
    new_files: list[Path] = field(default_factory=list)
    planned: list[FileMeta] = field(default_factory=list)  # dry-run listing
    downloaded: list[FileMeta] = field(default_factory=list)  # metadata of new_files
    failures: list[str] = field(default_factory=list)


# =============================================================================
# Feed parsing (pure; unit-testable on a saved feed)
# =============================================================================

def make_direct_download_link(url: str) -> str:
    """Same rewrite as fetch/dromic.py (copied to avoid importing Selenium)."""
    if "/document/d/" in url:
        file_id = url.split("/document/d/")[1].split("/")[0]
        return f"https://docs.google.com/document/d/{file_id}/export?format=docx"
    if "docs.google.com/viewer" in url:
        qs = parse_qs(urlparse(url).query)
        if "url" in qs:
            return unquote(qs["url"][0])
    return url


def extract_file_links(content_html: str) -> list[str]:
    """Report file URLs in post order (newest first), de-duplicated.

    Each `wp-block-file` block links its file twice (name + download button),
    and Google Viewer embeds wrap the same file again.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in _HREF.findall(content_html or ""):
        url = make_direct_download_link(html.unescape(raw))
        path = urlparse(url).path.lower()
        if not path.endswith(FILE_EXTENSIONS) and "docs.google.com" not in url:
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def parse_feed(xml_bytes: bytes) -> list[FeedItem]:
    root = ET.fromstring(xml_bytes)
    items: list[FeedItem] = []
    for it in root.iter("item"):
        pub = it.findtext("pubDate") or ""
        try:
            pub_dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
        except (TypeError, ValueError):
            pub_dt = datetime.now(timezone.utc)
        items.append(FeedItem(
            title=html.unescape((it.findtext("title") or "").strip()),
            link=(it.findtext("link") or "").strip(),
            guid=(it.findtext("guid") or "").strip(),
            pub_date=pub_dt,
            categories=[c.text.strip() for c in it.findall("category") if c.text],
            files=extract_file_links(it.findtext(CONTENT_NS) or ""),
        ))
    return items


def report_info(url: str) -> tuple[Optional[int], bool, Optional[str]]:
    """(report number, is terminal report, as-of text) from the file name."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    m = _REPORT_NO.search(name)
    a = _AS_OF.search(name)
    return (int(m.group(1)) if m else None, bool(_TERMINAL.search(name)), a.group(1) if a else None)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", text.lower().replace("ñ", "n"))
    return {w for w in words if w not in _STOPWORDS}


def title_matches_file(title: str, url: str) -> bool:
    """Guard against a post linking another event's file (the Goal 3 case
    where the Tandag post yielded the Butuan PDF): at least one place word of
    the post title must appear in the file name. Google links can't be checked.
    """
    if "docs.google.com" in url:
        return True
    title_words = _tokens(re.sub(r"\[.*?\]", "", title))
    if not title_words:
        return True
    return bool(title_words & _tokens(unquote(url.rsplit("/", 1)[-1])))


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "", name).strip()


# =============================================================================
# State files
# =============================================================================

def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"last_scrape_date": None, "entries": []}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):  # oldest manifest shape
        return {"last_scrape_date": None, "entries": data}
    data.setdefault("entries", [])
    return data


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic on the same volume


def load_state(path: Path) -> RssState:
    if not path.exists():
        return RssState()
    data = json.loads(path.read_text(encoding="utf-8"))
    return RssState(**data)


# =============================================================================
# Network
# =============================================================================

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def fetch_page(session: requests.Session, year: int, page: int, state: RssState | None) -> requests.Response:
    url = FEED_URL.format(year=year) + (f"?paged={page}" if page > 1 else "")
    headers = {}
    if page == 1 and state is not None:  # conditional GET only for the head page
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified
    r = session.get(url, headers=headers, timeout=TIMEOUT)
    if r.status_code not in (200, 304, 404):
        r.raise_for_status()
    return r


def download(session: requests.Session, url: str, dest_dir: Path) -> Path:
    """Download to a temp file, check it is a document, then move into place."""
    name = sanitize_filename(unquote(urlparse(url).path.rsplit("/", 1)[-1])) or "download.bin"
    if "docs.google.com" in url and not name.lower().endswith(FILE_EXTENSIONS):
        name = sanitize_filename(f"google-{abs(hash(url))}.docx")
    dest = dest_dir / name
    if dest.exists():  # same name, different URL (re-upload): keep both
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{suffix}"
            n += 1

    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    body = r.content
    if name.lower().endswith(".pdf") and not body.startswith(b"%PDF"):
        raise ValueError(f"not a PDF ({r.headers.get('content-type')}, {len(body)} bytes)")
    if name.lower().endswith(".docx") and not body.startswith(b"PK"):
        raise ValueError(f"not a DOCX ({r.headers.get('content-type')}, {len(body)} bytes)")

    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return dest


# =============================================================================
# Orchestration
# =============================================================================

def fetch(
    year: int,
    *,
    raw_root: Path = RAW_ROOT,
    dry_run: bool = False,
    all_reports: bool = False,
    max_pages: int = 5,
    force: bool = False,
) -> FetchResult:
    """Poll the feed and download report files not yet in the manifest.

    all_reports=False (default): only the newest file of each post. The
    pipeline keeps one report per event (the latest), so older reports are
    not needed for the knowledge graph.
    """
    out_dir = raw_root / str(year)
    manifest_path = out_dir / "manifest.json"
    state_path = out_dir / "rss_state.json"
    manifest = load_manifest(manifest_path)
    state = load_state(state_path)
    known = {e.get("download_url") for e in manifest["entries"]}
    result = FetchResult(year=year)
    session = _session()

    last_seen = datetime.fromisoformat(state.newest_pub_date) if state.newest_pub_date else None
    items: list[FeedItem] = []
    for page in range(1, max_pages + 1):
        r = fetch_page(session, year, page, None if force or dry_run else state)
        if r.status_code == 304:
            log.info("Feed not modified since last poll (HTTP 304)")
            result.not_modified = True
            return result
        if r.status_code == 404:  # past the last page
            break
        result.pages_read += 1
        page_items = parse_feed(r.content)
        if page == 1:
            etag, last_mod = r.headers.get("ETag"), r.headers.get("Last-Modified")
        items.extend(page_items)
        # Stop paging once this page reaches posts we already saw.
        if not page_items or last_seen is None or min(i.pub_date for i in page_items) <= last_seen:
            break

    result.items_seen = len(items)
    log.info("Feed %d: %d posts on %d page(s)", year, len(items), result.pages_read)

    for item in items:
        files = item.files if all_reports else item.files[:1]
        if not item.files:
            log.warning("No report file linked in post: %s", item.title)
        for url in files:
            if url in known:
                continue
            number, terminal, as_of = report_info(url)
            meta = FileMeta(
                download_url=url, filename="", post_title=item.title, post_url=item.link,
                guid=item.guid, pub_date=item.pub_date.isoformat(), categories=item.categories,
                report_number=number, terminal=terminal, report_as_of=as_of,
                title_check="ok" if title_matches_file(item.title, url) else "mismatch",
            )
            if meta.title_check == "mismatch":
                log.warning("File name does not match post title (check by hand): %s <- %s", url, item.title)
            if dry_run:
                meta.filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
                result.planned.append(meta)
                continue
            try:
                path = download(session, url, out_dir)
            except Exception as exc:  # keep going; next poll retries
                log.error("Download failed: %s (%s)", url, exc)
                result.failures.append(url)
                continue
            meta.filename = path.name
            manifest["entries"].append({
                "filename": path.name,
                "download_url": url,
                "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "post_url": item.link,
                "page": 0,  # 0 = fetched from RSS (Selenium used listing pages 1..n)
            })
            state.files[url] = asdict(meta)
            known.add(url)
            result.new_files.append(path)
            result.downloaded.append(meta)
            log.info("Downloaded %s", path.name)
            # Save after every file so an interrupted run never re-downloads.
            save_json(manifest_path, manifest)
            save_json(state_path, asdict(state))

    if not dry_run and result.pages_read:
        # Only advance the conditional-GET validators when every download
        # succeeded, so failed files are retried on the next poll.
        if not result.failures:
            state.etag, state.last_modified = etag, last_mod
        newest = max((i.pub_date for i in items), default=None)
        if newest and (last_seen is None or newest > last_seen) and not result.failures:
            state.newest_pub_date = newest.isoformat()
        manifest["last_scrape_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d, %H:%M:%S")
        save_json(manifest_path, manifest)
        save_json(state_path, asdict(state))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch new DROMIC reports from the RSS feed")
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--dry-run", action="store_true", help="List new files; download nothing")
    parser.add_argument("--all-reports", action="store_true",
                        help="Download every report file in each post, not only the newest")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--force", action="store_true", help="Ignore ETag/Last-Modified")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    result = fetch(args.year, dry_run=args.dry_run, all_reports=args.all_reports,
                   max_pages=args.max_pages, force=args.force)
    if args.dry_run:
        for m in result.planned:
            tag = "TERMINAL" if m.terminal else f"#{m.report_number}" if m.report_number else "-"
            print(f"[{m.title_check:8}] {tag:>9}  {m.filename}\n            post: {m.post_title}")
        print(f"\n{len(result.planned)} new file(s) would be downloaded "
              f"({result.items_seen} posts, {result.pages_read} page(s))")
    else:
        print(f"{len(result.new_files)} new file(s), {len(result.failures)} failure(s)"
              + (" (feed not modified)" if result.not_modified else ""))
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
