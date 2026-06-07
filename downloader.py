"""
Headless document downloader. Loads saved auth state and downloads documents
matching the configured URL pattern from the target page.
"""
import math
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, unquote
from playwright.sync_api import sync_playwright, Page, BrowserContext

from auth import AUTH_STATE_FILE, clear_auth


@dataclass
class PaginationInfo:
    page_first: int   # first item on current page (1-based)
    page_last: int    # last item on current page
    total: int        # total number of documents
    items_per_page: int
    total_pages: int


def parse_pagination(page: Page, span_pattern: str = r"Dokumenty (\d+)\s*[-–]\s*(\d+)\s+z\s+(\d+)") -> PaginationInfo:
    """
    Locate a <span> whose text matches e.g. "Dokumenty 1 - 5 z 163"
    and return structured pagination info.
    """
    spans = page.eval_on_selector_all("span", "els => els.map(el => el.textContent)")
    for text in spans:
        m = re.search(span_pattern, text or "")
        if m:
            first, last, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
            per_page = 5 #last - first + 1
            return PaginationInfo(
                page_first=first,
                page_last=last,
                total=total,
                items_per_page=per_page,
                total_pages=math.ceil(total / per_page),
            )
    raise RuntimeError("Could not find pagination span on the page.")


def _filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name or "document"


def _already_downloaded(dest: Path) -> bool:
    return dest.exists()


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _normalize_filename(name: str) -> str:
    name = unquote(name)
    name = _sanitize_filename(name)
    if len(name) > 245:
        ext = Path(name).suffix
        name = Path(name).stem[:245 - len(ext)] + ext
    return name


def _filename_from_content_disposition(header: str) -> str:
    # Prefer filename*= (RFC 5987, percent-encoded UTF-8)
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;\s]+)", header, re.IGNORECASE)
    if m:
        return unquote(m.group(1))
    # Fall back to filename=
    m = re.search(r'filename\s*=\s*"?([^";\r\n]+)"?', header, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return ""


def _extract_asice(path: Path) -> list[Path]:
    with zipfile.ZipFile(path) as zf:
        entries = [
            name for name in zf.namelist()
            if name != "mimetype" and not name.startswith("META-INF/")
        ]
        if not entries:
            print(f"  WARNING  {path.name}: no document found inside, keeping as-is")
            return [path]
        extracted = []
        for i, entry in enumerate(entries):
            suffix = f"_{i + 1}" if len(entries) > 1 else ""
            dest = path.with_name(path.stem + suffix + Path(entry).suffix)
            dest.write_bytes(zf.read(entry))
            print(f"  extracted  {entry} → {dest.name}")
            extracted.append(dest)
    path.unlink()
    return extracted


def _rename_xdcf(path: Path) -> None:
    if path.suffix.lower() == ".xdcf":
        new_path = path.with_name(path.name + ".html")
        path.rename(new_path)
        print(f"  renamed  {path.name} → {new_path.name}")


def postprocess_downloads(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        if path.suffix.lower() == ".asice":
            print(f"  unzipping  {path.name}")
            for extracted in _extract_asice(path):
                _rename_xdcf(extracted)
        else:
            _rename_xdcf(path)


def _collect_all_links(
    page: Page,
    pagination: PaginationInfo,
    documents_url: str,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[tuple[str, str, str]]:
    """Navigate each page via ?page=N and return (download_url, date_prefix, fallback_name) tuples."""
    collected: list[tuple[str, str, str]] = []
    page_range = list(range(pagination.total_pages, 0, -1))
    total_steps = len(page_range)

    last_date_str = ""
    date_i = 1
    for step, page_num in enumerate(page_range, 1):
        print(f"  Scanning page {page_num}/{pagination.total_pages} ...")
        page.goto(f"{documents_url}?page={page_num}", wait_until="networkidle")
        page.wait_for_selector("ul li")

        items = page.eval_on_selector_all(
            "ul li a[href]",
            """els => els.map(el => {
                const li = el.closest('li') || el;
                const dateSpan = Array.from(li.querySelectorAll('span'))
                    .find(s => /\\d{2}\\.\\d{2}\\.\\d{4}/.test(s.textContent));
                return {
                    href: el.href,
                    name: (el.querySelector('h4') || {}).textContent || '',
                    date: dateSpan ? dateSpan.textContent.trim() : ''
                };
            })""",
        )

        link_pattern = re.compile(re.escape(documents_url) + r"/.*")
        for item in reversed(items):
            href, name, date = item["href"], item["name"].strip(), item["date"]
            if not re.search(link_pattern, href):
                continue
            download_url = (
                "https://obcan.justice.sk/pilot/api/essp-service/api/"
                + href.split("https://obcan.justice.sk/sudny-spis/")[1].split("?")[0]
                + "/download"
            )
            if date:
                day, month, year = date.split(".")
                date_str = f"{year}-{month}-{day}"
            else:
                date_str = ""
            if date_str != last_date_str:
                last_date_str = date_str
                date_i = 1
            else:
                date_i += 1
            date_prefix = date_str + "_" + str(date_i).zfill(2) + "_"
            fallback = _sanitize_filename(name) if name else _filename_from_url(download_url)
            collected.append((download_url, date_prefix, fallback))

        print(f"    {len(items)} link(s) found")
        if progress_cb:
            progress_cb(step, total_steps)

    # Deduplicate by URL while preserving order
    seen: set[str] = set()
    unique = [t for t in collected if not (t[0] in seen or seen.add(t[0]))]
    return unique


def download_documents(
    documents_url: str,
    download_dir: str,
    headless: bool = True,
    collect_progress_cb: Callable[[int, int], None] | None = None,
    download_progress_cb: Callable[[int, int], None] | None = None,
) -> list[Path]:
    dest_dir = Path(download_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context: BrowserContext = browser.new_context(
            storage_state=AUTH_STATE_FILE,
            accept_downloads=True,
        )
        page = context.new_page()

        print(f"Navigating to {documents_url} ...")
        page.goto(documents_url, wait_until="networkidle")

        #page.wait_for_timeout(15_000)

        page.wait_for_selector("span:has-text('Dokumenty')")
        pagination = parse_pagination(page)
        print(
            f"Pagination: {pagination.total} documents total, "
            f"{pagination.items_per_page} per page → {pagination.total_pages} page(s)"
        )

        doc_links = _collect_all_links(page, pagination, documents_url, collect_progress_cb)
        print(f"\nCollected {len(doc_links)} unique document link(s) across all pages")

        total_links = len(doc_links)
        for i, (url, date_prefix, fallback) in enumerate(doc_links, 1):
            try:
                response = context.request.get(url)
                if not response.ok:
                    print(f"  ERROR {url}: HTTP {response.status}")
                    continue

                cd = response.headers.get("content-disposition", "")
                cd_name = _normalize_filename(_filename_from_content_disposition(cd))
                base = cd_name if cd_name else fallback
                filename = date_prefix + base
                dest = dest_dir / filename

                if _already_downloaded(dest):
                    print(f"  skip  {filename} (already downloaded)")
                    continue

                print(f"  ↓     {filename}")
                dest.write_bytes(response.body())
                downloaded.append(dest)
            except Exception as exc:
                print(f"  ERROR downloading {url}: {exc}")
            finally:
                if download_progress_cb:
                    download_progress_cb(i, total_links)

        browser.close()

    postprocess_downloads(downloaded)
    return downloaded
