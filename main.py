"""
SpisDownloader — downloads documents from a 2FA-protected webpage.

Usage:
    python main.py            # open GUI
    python main.py --cli      # run without GUI (authenticate if needed, then download)
    python main.py --reauth   # CLI: force a fresh login
    python main.py --debug    # CLI: keep browser window visible during download
"""
import argparse
import os
from dotenv import load_dotenv

load_dotenv()


def _run_cli(reauth: bool, debug: bool) -> None:
    from auth import authenticate, needs_auth, clear_auth
    from downloader import download_documents

    login_url = os.environ["LOGIN_URL"]
    documents_url = os.environ["DOCUMENTS_URL"]
    download_dir = os.getenv("DOWNLOAD_DIR", "./downloads")
    logged_in_selector = os.getenv("LOGGED_IN_SELECTOR")

    if reauth:
        clear_auth()

    if needs_auth():
        authenticate(login_url, logged_in_selector)

    try:
        downloaded = download_documents(
            documents_url=documents_url,
            login_url=login_url,
            download_dir=download_dir,
            headless=not debug,
        )
        print(f"\nDone. {len(downloaded)} file(s) downloaded to {download_dir}")
    except RuntimeError as exc:
        print(f"\n{exc}")
        print("Run the script again to re-authenticate.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download documents from a 2FA-protected site.")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode without GUI")
    parser.add_argument("--reauth", action="store_true", help="Force re-authentication (CLI only)")
    parser.add_argument("--debug", action="store_true", help="Keep browser visible (CLI only)")
    args = parser.parse_args()

    if args.cli:
        _run_cli(reauth=args.reauth, debug=args.debug)
    else:
        from gui import run_gui
        run_gui()


if __name__ == "__main__":
    main()
