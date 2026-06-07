# Slovak Court File Downloader (Súdny spis)

## Description

Tool that automatically downloads all documents from the webpage of Slovakian Court File (Súdný spis).
The user just selects the webpage of his/her file - e.g. https://obcan.justice.sk/sudny-spis/spisy/12345678/dokumenty
and the tool automatically downloads all files to a folder.

# Technical description

Downloads documents from a 2FA-protected webpage of Slovakian Cour File (Súdný spis) https://obcan.justice.sk/sudny-spis/spisy/xxxxxxxx/dokumenty
For the automated access to the webpage is used the Playwright.
Authentication is performed once in a visible browser window; all subsequent runs use the saved session and operate headlessly.

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
# Edit .env with your values
```

## Configuration

| Variable | Required | Description |
|---|---|---|
| `LOGIN_URL` | yes | URL of the login page |
| `DOCUMENTS_URL` | yes | URL of the paginated documents list |
| `DOWNLOAD_DIR` | no | Download destination (default: `./downloads`) |
| `LOGGED_IN_SELECTOR` | no | CSS selector present only when logged in; used to detect a valid session |
| `LINK_PATTERN` | no | Regex matched against `<a href>` inside list items (default: `\.pdf$`) |

## Usage

```bash
# First run: opens a browser window for login + 2FA, then downloads headlessly
python main.py

# Force re-authentication (e.g. after session expires)
python main.py --reauth

# Debug mode: keep browser visible during the download phase
python main.py --debug
```

## How it works

1. **Authentication** — a real Chromium window opens at `LOGIN_URL`. After you log in and complete 2FA, the session (cookies + localStorage) is saved to `auth_state.json`.
2. **Pagination** — a headless browser loads `DOCUMENTS_URL`, reads the `"Dokumenty N – M z TOTAL"` span to determine the total number of pages, then visits each page via `?page=N`.
3. **Link collection** — on each page, `<ul><li><a>` elements are scraped for the document URL, filename (from `<h4>`), and date (from a `DD.MM.YYYY` span).
4. **Download** — each document is fetched via `context.request.get()` using the live session cookies and written to `DOWNLOAD_DIR`. Already-downloaded files are skipped.
5. **Post-processing** — ASICE files are extracted.

## File naming

Downloaded files are named `YYYY-MM-DD_NN_<title>.<ext>`, where:
- `YYYY-MM-DD` is the document date converted from `DD.MM.YYYY`
- `NN` is a counter disambiguating multiple documents on the same date
- `<title>` is the sanitized `<h4>` text
- `<ext>` is detected from magic bytes after download
