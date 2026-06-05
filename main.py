"""
SpisDownloader — downloads documents from a 2FA-protected webpage.

Usage:
    python main.py            # authenticate (if needed) then download
    python main.py --reauth   # force a fresh login even if a session exists
"""
import argparse
import os
from dotenv import load_dotenv

from auth import authenticate, needs_auth, clear_auth
from downloader import download_documents

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download documents from a 2FA-protected site.")
    parser.add_argument("--reauth", action="store_true", help="Force re-authentication")
    parser.add_argument("--debug", action="store_true", help="Show browser window for debugging")
    args = parser.parse_args()

    login_url = os.environ["LOGIN_URL"]
    documents_url = os.environ["DOCUMENTS_URL"]
    download_dir = os.getenv("DOWNLOAD_DIR", "./downloads")
    logged_in_selector = os.getenv("LOGGED_IN_SELECTOR")

    if args.reauth:
        clear_auth()

    if needs_auth():
        authenticate(login_url, logged_in_selector)

    try:
        downloaded = download_documents(
            documents_url=documents_url,
            login_url=login_url,
            download_dir=download_dir,
            headless=not args.debug,
        )
        print(f"\nDone. {len(downloaded)} file(s) downloaded to {download_dir}")
    except RuntimeError as exc:
        # Session expired mid-run; cleared automatically — ask user to re-run
        print(f"\n{exc}")
        print("Run the script again to re-authenticate.")


if __name__ == "__main__":
    main()
