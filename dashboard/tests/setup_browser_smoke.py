"""Real HTTPS guest UI with synthetic browser data, never a Facebook login."""
import base64


class OfflineBrowser:
    def __init__(self):
        self.frame, self.sessions, self.calls = "", set(), []

    def call(self, ident, action, data=None):
        self.calls.append((ident, action, data))
        if action == "start":
            self.sessions.add(ident)
        elif action == "stop":
            self.sessions.discard(ident)
        elif action == "capture":
            assert ident in self.sessions
            return {"cookies": {"c_user": "7766554433", "xs": "SYNTHETIC_GUEST_SESSION", "datr": "SYNTHETIC_GUEST_DEVICE"}}
        elif action == "frame":
            assert ident in self.sessions
            return {"jpeg": self.frame, "site": "offline.invalid", "ready": True, "width": 1024, "height": 768}
        return {"started": True}


def verify(browser, admin, origin, store, offline, output):
    fixture = browser.new_page(viewport={"width": 1024, "height": 768})
    fixture.set_content("<body style='margin:0;background:#edf2ed;color:#173626;font-family:Arial;padding:70px'><p>OFFLINE BROWSER FIXTURE · SYNTHETIC DATA</p><h1>A real browser, a private connection.</h1><p>This screenshot tests the remote viewport. No Facebook login took place.</p><label>Example field <input value='Synthetic input only'></label></body>")
    offline.frame = base64.b64encode(fixture.screenshot(type="jpeg")).decode()
    fixture.close()
    admin.set_viewport_size({"width": 1440, "height": 1100})
    admin.goto(origin + "/setup-links")
    admin.get_by_label("Label", exact=False).fill("For Quinn")
    admin.get_by_role("button", name="Generate setup link").click()
    link = admin.locator("#setup-invite").input_value()
    assert "#" in link and "?" not in link
    admin.screenshot(path=str(output / "setup-links.png"), full_page=True)
    guest_context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1100})
    guest_context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
    guest = guest_context.new_page()
    errors = []
    guest.on("pageerror", lambda error: errors.append(str(error)))
    guest.goto(link)
    guest.screenshot(path=str(output / "guest-welcome.png"), full_page=True)
    assert not any(row["name"] == "Quinn" for row in store.accounts())
    guest.set_viewport_size({"width": 390, "height": 844})
    guest.screenshot(path=str(output / "guest-mobile.png"), full_page=True)
    assert not guest.evaluate("document.documentElement.scrollWidth > innerWidth")
    guest.locator("#guest-name").fill("Quinn")
    guest.locator("#guest-consent").check()
    guest.get_by_role("button", name="Continue to Facebook").click()
    guest.get_by_role("button", name="Open Facebook", exact=False).click()
    guest.locator("#remote-screen").wait_for(state="visible")
    guest.locator("#remote-typing").fill("SYNTHETIC_REMOTE_INPUT")
    guest.set_viewport_size({"width": 1280, "height": 1100})
    guest.screenshot(path=str(output / "guest-browser.png"), full_page=True)
    guest.locator("#approve-session").check()
    guest.get_by_role("button", name="Connect & continue").click()
    guest.locator("#telegram-invite").wait_for(state="visible")
    telegram = guest.locator("#telegram-invite").get_attribute("href")
    assert telegram.startswith("https://t.me/")
    assert "SYNTHETIC_GUEST_SESSION" not in guest.content()
    assert any(call[1] == "text" and call[2]["text"] == "SYNTHETIC_REMOTE_INPUT" for call in offline.calls)
    guest.screenshot(path=str(output / "guest-telegram.png"), full_page=True)
    assert store.claim_invite(telegram.split("start=")[1], 55443322, "@synthetic_quinn")
    account = next(row for row in store.accounts() if row["name"] == "Quinn")
    admin.goto(origin + "/accounts/" + account["id"])
    admin.get_by_role("button", name="Approve pairing").click()
    admin.get_by_role("button", name="Start", exact=True).click()
    assert store.account(account["id"])["desired"] == 1
    admin.goto(origin + "/setup-links")
    admin.get_by_label("Label", exact=False).fill("Disable this invitation")
    admin.get_by_role("button", name="Generate setup link").click()
    second = admin.locator("#setup-invite").input_value()
    second_context = browser.new_context(ignore_https_errors=True)
    second_context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
    other = second_context.new_page()
    other.goto(link)
    other.locator("#guest-name").fill("Another person")
    other.locator("#guest-consent").check()
    other.get_by_role("button", name="Continue to Facebook").click()
    other.locator("#setup-error").wait_for(state="visible")
    assert "already used" in other.locator("#setup-error").inner_text()
    other.goto(second)
    other.locator("#guest-name").fill("Disable test")
    other.locator("#guest-consent").check()
    other.get_by_role("button", name="Continue to Facebook").click()
    other.get_by_role("button", name="Open Facebook", exact=False).click()
    other.locator("#remote-screen").wait_for(state="visible")
    assert offline.sessions
    admin.goto(origin + "/setup-links")
    row = admin.locator(".invitation-row").filter(has_text="Disable this invitation")
    admin.once("dialog", lambda dialog: dialog.accept())
    row.get_by_role("button", name="Disable link").click()
    other.locator("#setup-error").wait_for(state="visible")
    assert "disabled" in other.locator("#setup-error").inner_text()
    assert not offline.sessions
    assert not errors, errors
    guest_context.close()
    second_context.close()
