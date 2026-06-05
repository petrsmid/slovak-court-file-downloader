"""
Headless document downloader. Loads saved auth state and downloads documents
matching the configured URL pattern from the target page.
"""
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
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


def _is_session_valid(page: Page, login_url: str) -> bool:
    """Returns False if we were redirected to the login page."""
    return login_url not in page.url


def _filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name or "document"


def _already_downloaded(dest: Path) -> bool:
    return dest.exists()


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _detect_extension(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:2] == b"PK":
        return ".zip"
    # HTML: check first non-whitespace content
    sniff = data[:512].lstrip()
    if sniff[:14].upper() == b"<GeneralAgenda":
        return ".xml"
    if sniff[:9].upper() == b"<!DOCTYPE" or sniff[:5].upper() == b"<HTML" or sniff[:1].upper() == b"<":
        return ".html"
    return ".bin"


def _extract_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        pdfs = [name for name in zf.namelist() if name.lower().endswith(".pdf")]
        if not pdfs:
            print(f"  WARNING  {path.name}: ZIP contains no PDFs, keeping as-is")
            return
        for i, pdf_name in enumerate(pdfs):
            suffix = f"_{i + 1}" if len(pdfs) > 1 else ""
            dest = path.with_name(path.stem + suffix + ".pdf")
            dest.write_bytes(zf.read(pdf_name))
            print(f"  extracted  {pdf_name} → {dest.name}")
    path.unlink()


def postprocess_downloads(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        ext = _detect_extension(path.read_bytes())
        if ext == ".zip":
            print(f"  unzipping  {path.name}")
            renamed = path.with_suffix(".zip")
            if path != renamed:
                path.rename(renamed)
                path = renamed
            _extract_zip(path)
        elif path.suffix.lower() != ext:
            new_path = path.with_suffix(ext)
            path.rename(new_path)
            print(f"  renamed  {path.name} → {new_path.name}")


def _collect_all_links(
    page: Page,
    pagination: PaginationInfo,
    documents_url: str,
) -> list[tuple[str, str]]:
    """Navigate each page via ?page=N and return (download_url, filename) pairs from ul > li items."""
    collected: list[tuple[str, str]] = []

    last_date_str = ""
    date_i = 1
    for page_num in range(pagination.total_pages, 30, -1):
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
            base = date_prefix + (_sanitize_filename(name) if name else _filename_from_url(download_url))
            collected.append((download_url, base))

        print(f"    {len(items)} link(s) found")

    # Deduplicate by URL while preserving order
    seen: set[str] = set()
    unique = [(url, name) for url, name in collected if not (url in seen or seen.add(url))]
    return unique


def download_documents(
    documents_url: str,
    login_url: str,
    download_dir: str,
    headless: bool = True,
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

        page.wait_for_timeout(15_000)

        if not _is_session_valid(page, login_url):
            browser.close()
            clear_auth()
            raise RuntimeError(
                "Session expired — auth state cleared. Re-run to authenticate again."
            )

        page.wait_for_selector("span:has-text('Dokumenty')")
        pagination = parse_pagination(page)
        print(
            f"Pagination: {pagination.total} documents total, "
            f"{pagination.items_per_page} per page → {pagination.total_pages} page(s)"
        )

        doc_links = _collect_all_links(page, pagination, documents_url)
        print(f"\nCollected {len(doc_links)} unique document link(s) across all pages")

        for url, filename in doc_links:
            dest = dest_dir / filename

            if _already_downloaded(dest):
                print(f"  skip  {filename} (already downloaded)")
                continue

            print(f"  ↓     {filename}")
            try:
                response = context.request.get(url)
                if not response.ok:
                    print(f"  ERROR {url}: HTTP {response.status}")
                    continue
                dest.write_bytes(response.body())
                downloaded.append(dest)
            except Exception as exc:
                print(f"  ERROR downloading {url}: {exc}")

        browser.close()

    postprocess_downloads(downloaded)
    return downloaded
