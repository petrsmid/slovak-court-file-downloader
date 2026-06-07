# Slovak Court File Downloader (Súdny spis)

## Description

Tool that automatically downloads all documents from the webpage of a Slovakian Court File (Súdny spis).
The user pastes the URL of their file's documents page (e.g. `https://obcan.justice.sk/sudny-spis/spisy/12345678/dokumenty`) and the tool downloads all files to a selected folder.

## Requirements

- Python 3.11+
- Chromium (installed automatically by Playwright)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env — at minimum set LOGIN_URL
```

## Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `LOGIN_URL` | yes | URL of the login page |
| `DOCUMENTS_URL` | no | Pre-fills the URL field in the GUI |
| `DOWNLOAD_DIR` | no | Pre-fills the download folder in the GUI (default: `./downloads`) |
| `LOGGED_IN_SELECTOR` | no | CSS selector present only when logged in, used to detect a valid session |

## Usage

### GUI (default)

```bash
python main.py
```

A window opens with:
- **Documents URL** — paste the `/dokumenty` URL of your court file
- **Download folder** — choose where to save files (Browse button opens a folder dialog)
- **Force re-authentication** — checked by default; clears the saved session and opens a fresh login browser
- **Run download** — starts the process; two progress bars show collection and download progress
- **Download finished** — dialog shown on completion

### CLI

```bash
python main.py --cli                  # use URLs from .env
python main.py --cli --reauth         # force fresh login
python main.py --cli --debug          # keep browser visible during download
```

## How it works

1. **Authentication** — a visible Chromium window opens at `LOGIN_URL`. The window stays open until the text "Súdny spis" appears on the page (confirming login + 2FA are complete). The session (cookies + localStorage) is then saved to `auth_state.json` and the window closes.

2. **Pagination** — a headless browser loads the documents URL and reads the `"Dokumenty N – M z TOTAL"` span to determine the total number of pages. Each page is then fetched via `?page=N`.

3. **Link collection** — on each page, `<ul><li><a>` elements are scraped for the document URL, filename (from `<h4>`), and date (from a `DD.MM.YYYY` span).

4. **Download** — each document is fetched via `context.request.get()` using the live session cookies. The filename is taken from the `Content-Disposition` response header (with fallback to the scraped `<h4>` text). Already-downloaded files are skipped.

5. **Post-processing** — `.asice` containers (ASiC-E digital signature format) are unpacked: `META-INF/` and `mimetype` entries are skipped and the enclosed document is extracted.

## File naming

Downloaded files are named `YYYY-MM-DD_NN_<title>.<ext>`, where:
- `YYYY-MM-DD` is the document date (converted from the `DD.MM.YYYY` span)
- `NN` is a zero-padded counter disambiguating multiple documents on the same date
- `<title>` is the filename from `Content-Disposition` (or the `<h4>` text as fallback)
- Percent-encoded characters are decoded
- Names longer than 245 characters are truncated, preserving the extension
- Files with extension `.xdcf` get `.html` appended (`.xdcf.html`) for browser compatibility
