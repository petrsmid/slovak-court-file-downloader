"""
Handles the interactive (visible) browser authentication flow.
Opens a real browser window so the user can log in and complete 2FA,
then saves the resulting session state for headless reuse.
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, Page

AUTH_STATE_FILE = "auth_state.json"


def _is_logged_in(page: Page, login_url: str, logged_in_selector: str | None) -> bool:
    if logged_in_selector:
        return page.query_selector(logged_in_selector) is not None
    return login_url not in page.url


def authenticate(login_url: str, logged_in_selector: str | None = None) -> None:
    """
    Open a visible browser at login_url and wait for the user to complete
    login + 2FA. Saves auth state to AUTH_STATE_FILE when done.
    """
    print("Opening browser for authentication...")
    print("Please log in and complete 2FA. The window will close automatically once you're in.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url)

        # Poll until the user is logged in (navigated away from login page)
        page.wait_for_function(
            """(loginUrl) => !window.location.href.includes(loginUrl)""",
            arg=login_url,
            timeout=300_000,  # 5 minutes to complete login + 2FA
        )

        # Give the page a moment to settle (load post-login redirects / tokens)
        page.wait_for_load_state("networkidle", timeout=15_000)

        context.storage_state(path=AUTH_STATE_FILE)
        browser.close()

    print(f"Authentication successful. Session saved to {AUTH_STATE_FILE}")


def needs_auth() -> bool:
    return not Path(AUTH_STATE_FILE).exists()


def clear_auth() -> None:
    if Path(AUTH_STATE_FILE).exists():
        os.remove(AUTH_STATE_FILE)
        print(f"Removed {AUTH_STATE_FILE}")
