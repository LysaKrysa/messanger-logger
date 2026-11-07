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
    fixture.set_content("""<body style='margin:0;background:#edf2ed;color:#173626;font-family:Arial;padding:70px'>
<p>OFFLINE BROWSER FIXTURE · SYNTHETIC DATA</p><h1>A real browser, a private connection.</h1>
<p>This screenshot tests the remote viewport. No Facebook login took place.</p>
<label>Example field <input value='Synthetic input only'></label>
<button id='target-a' style='position:absolute;left:92px;top:288px;width:24px;height:24px;padding:0' onclick='this.dataset.hits=String(Number(this.dataset.hits||0)+1)'>A</button>
<button id='target-b' style='position:absolute;left:500px;top:372px;width:24px;height:24px;padding:0' onclick='this.dataset.hits=String(Number(this.dataset.hits||0)+1)'>B</button>
<button id='target-c' style='position:absolute;left:908px;top:664px;width:24px;height:24px;padding:0' onclick='this.dataset.hits=String(Number(this.dataset.hits||0)+1)'>C</button></body>""")
    offline.frame = base64.b64encode(fixture.screenshot(type="jpeg")).decode()
    admin.set_viewport_size({"width": 1440, "height": 1100})
    admin.goto(origin + "/setup-links")
    admin.get_by_label("Label", exact=False).fill("For Quinn")
    admin.get_by_role("button", name="Generate setup link").click()
    link = admin.locator("#setup-invite").input_value()
    assert "#" in link and "?" not in link
    admin.screenshot(path=str(output / "setup-links.png"), full_page=True)
    guest_context = browser.new_context(ignore_https_errors=True, has_touch=True, viewport={"width": 1440, "height": 1100})
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
    guest.wait_for_function("() => document.getElementById('remote-screen').naturalWidth > 0")
    count_frames = lambda: sum(action == "frame" and not (data or {}).get("metadata_only") for _, action, data in offline.calls)
    before = count_frames()
    guest.wait_for_timeout(900)
    assert count_frames() - before >= 4, "Live viewer did not deliver multiple frames per second"
    guest.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
    guest.wait_for_timeout(400)
    paused = count_frames()
    guest.wait_for_timeout(300)
    assert count_frames() == paused, "Hidden viewer continued capturing frames"
    guest.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
    guest.wait_for_function("() => document.getElementById('remote-screen').naturalWidth > 0")
    # The displayed frame and remote browser have different coordinate spaces.
    # Apply the UI's actual submitted clicks to small targets in the source page;
    # an incorrect scale misses the real button rather than only failing a formula.
    for index, (width, zoom, touch) in enumerate(((390, 1, True), (1440, 1, False), (1280, .8, False), (1024, 1.25, False)), 1):
        guest.set_viewport_size({"width": width, "height": 1100})
        guest.evaluate("zoom => document.documentElement.style.zoom = String(zoom)", zoom)
        screen = guest.locator("#remote-screen")
        for target in ("target-a", "target-b", "target-c"):
            source = fixture.locator("#" + target).bounding_box()
            displayed = screen.bounding_box()
            x, y = source["x"] + source["width"] / 2, source["y"] + source["height"] / 2
            before_click = len(offline.calls)
            position = {"x": x * displayed["width"] / 1024, "y": y * displayed["height"] / 768}
            with guest.expect_response(lambda response: response.url.endswith("/setup/api/input") and response.request.method == "POST") as received:
                if touch:
                    screen.tap(position=position)
                else:
                    screen.click(position=position)
            assert received.value.ok
            events = [event for _, action, payload in offline.calls[before_click:] if action == "input" for event in payload["events"]]
            clicks = [event for event in events if event["action"] == "click"]
            assert len(clicks) == 1, "A displayed click was lost or duplicated"
            fixture.mouse.click(clicks[0]["x"], clicks[0]["y"])
            assert fixture.locator("#" + target).get_attribute("data-hits") == str(index), "A scaled viewer click missed its source target"
    guest.evaluate("document.documentElement.style.zoom = ''")
    fixture.close()
    guest.locator("#remote-typing").fill("SYNTHETIC_REMOTE_INPUT")
    guest.set_viewport_size({"width": 1280, "height": 1100})
    guest.screenshot(path=str(output / "guest-browser.png"), full_page=True)
    guest.locator("#approve-session").check()
    guest.get_by_role("button", name="Connect & continue").click()
    guest.locator("#telegram-invite").wait_for(state="visible")
    telegram = guest.locator("#telegram-invite").get_attribute("href")
    assert telegram.startswith("https://t.me/")
    assert "SYNTHETIC_GUEST_SESSION" not in guest.content()
    assert any(event.get("action") == "text" and event.get("text") == "SYNTHETIC_REMOTE_INPUT" for _, action, payload in offline.calls if action == "input" for event in payload["events"])
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
