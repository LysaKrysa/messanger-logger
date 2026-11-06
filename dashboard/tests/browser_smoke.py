"""Real-browser UI verification with synthetic data; no Facebook/Telegram network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time

from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from dashboard.store import Store
from dashboard.web import create_app
from dashboard.tests.setup_browser_smoke import OfflineBrowser, verify as verify_setup


def main():
    output = Path(os.environ.get("DASHBOARD_SCREENSHOTS", "dashboard-screenshots"))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = Store(root / "state", Fernet.generate_key())
        store.set_password("synthetic-browser-password")
        store.configure_bot("123456:" + "SYNTHETIC_BOT_TOKEN_NOT_REAL" * 2,
                            {"id": 123456, "username": "workspace_example_bot", "has_topics_enabled": True})
        status = root / "status"
        status.mkdir()
        for index, name in enumerate(("My account", "Alex", "Maya")):
            ident = store.add_account(name, json.dumps({"c_user": str(111222333 + index), "xs": "SYNTHETIC", "datr": "SYNTHETIC"}))
            invite = store.invite(ident).split("start=")[1]
            store.claim_invite(invite, 87654321 + index, "@synthetic_friend")
            if index < 2:
                store.action(ident, "approve")
                store.action(ident, "start")
            (status / (ident + ".json")).write_text(json.dumps({"state": "running" if index < 2 else "stopped", "ready": index < 2, "updated": time.time()}))
        (status / "host.json").write_text(json.dumps({"updated": time.time(), "disk_free": int(38.4 * 1024 ** 3)}))
        store.set_setting("bot_status", "Invitation receiver connected")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = "https://127.0.0.1:" + str(port)
        offline = OfflineBrowser()
        app = create_app(store, origin=origin, status_root=status, browser_client=offline)
        server = make_server("127.0.0.1", port, app, ssl_context="adhoc", threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                executable = os.environ.get("DASHBOARD_TEST_BROWSER") or shutil.which("google-chrome") or shutil.which("chromium")
                options = {"executable_path": executable} if executable else {}
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"], **options)
                context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1050})
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/login")
                # Headless browsers may omit the automatic favicon fetch made
                # by a normal browser's tab UI. Exercise it explicitly.
                assert page.request.get(origin + "/favicon.ico").status == 204
                page.get_by_label("Administrator password").fill("synthetic-browser-password")
                page.get_by_role("button", name="Sign in").click()
                page.wait_for_url(origin + "/")
                page.screenshot(path=str(output / "desktop.png"), full_page=True)
                page.get_by_role("link", name="Add account", exact=False).click()
                page.get_by_label("Account label").fill("Jordan")
                page.locator("#cookies").fill(json.dumps({"c_user": "444000887", "xs": "SYNTHETIC_BROWSER_PRIVATE", "datr": "SYNTHETIC_BROWSER_PRIVATE"}))
                page.get_by_role("button", name="Create account").click()
                account_url = page.url
                ident = account_url.rsplit("/", 1)[-1]
                assert "SYNTHETIC_BROWSER_PRIVATE" not in page.content()
                page.get_by_role("button", name="Create invitation").click()
                token = page.locator("#invite").input_value().split("start=")[1]
                assert store.claim_invite(token, 99999888, "@synthetic_jordan")
                page.goto(account_url)
                page.get_by_role("button", name="Approve pairing").click()
                page.get_by_role("button", name="Start", exact=True).click()
                assert store.account(ident)["desired"] == 1
                page.screenshot(path=str(output / "account.png"), full_page=True)
                page.get_by_role("link", name="← All accounts").click()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "mobile.png"), full_page=True)
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "Mobile horizontal overflow"
                assert not errors, errors
                verify_setup(browser, page, origin, store, offline, output)
                browser.close()
            print("Browser smoke test passed: sign-in, account creation, hidden cookies, invitation, approval, start, and mobile layout.")
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
