"""Mobile-sized keyboard proxy checks; physical OS keyboard needs phone retest."""
import base64


def verify(browser, admin, origin, offline, output, engine):
    fixture = browser.new_page(viewport={"width": 432, "height": 800})
    fixture.set_content("<body style='margin:0;background:#f1f5ee;font-family:Arial;padding:25px'><p>OFFLINE MOBILE INPUT FIXTURE</p><h1>Keep your place.</h1><label>Example field <input value='Synthetic input only'></label><p>No Facebook login or physical keyboard is tested here.</p></body>")
    offline.frame = base64.b64encode(fixture.screenshot(type="jpeg")).decode()
    offline.width, offline.height = 432, 800
    fixture.close()
    admin.goto(origin + "/setup-links")
    admin.get_by_label("Label", exact=False).fill("Mobile keyboard " + engine)
    admin.get_by_role("button", name="Generate setup link").click()
    link = admin.locator("#setup-invite").input_value()
    context = browser.new_context(ignore_https_errors=True, is_mobile=True, has_touch=True,
                                  device_scale_factor=2, viewport={"width": 390, "height": 844})
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
    guest = context.new_page()
    errors = []
    guest.on("pageerror", lambda error: errors.append(str(error)))
    try:
        guest.goto(link)
        guest.locator("#guest-name").fill("Keyboard fixture")
        guest.locator("#guest-consent").check()
        guest.get_by_role("button", name="Continue to Facebook").click()
        guest.get_by_role("button", name="Open Facebook", exact=False).click()
        guest.wait_for_function("() => document.getElementById('remote-screen').naturalWidth === 432")
        guest.evaluate("window.scrollTo(0, document.getElementById('remote-screen').getBoundingClientRect().top + scrollY - 80)")
        guest.wait_for_timeout(100)
        anchor = guest.evaluate("scrollY")
        frame = guest.locator("#remote-screen").bounding_box()
        guest.touchscreen.tap(frame["x"] + frame["width"] / 2, frame["y"] + 150)
        guest.wait_for_function("() => document.activeElement.id === 'remote-typing'")
        guest.wait_for_timeout(100)
        assert abs(guest.evaluate("scrollY") - anchor) <= 2, "Focusing the keyboard moved the page"
        proxy = guest.locator("#remote-typing")
        style = proxy.evaluate("element => ({position:getComputedStyle(element).position, opacity:getComputedStyle(element).opacity})")
        assert style == {"position": "fixed", "opacity": "0"}
        box = proxy.bounding_box()
        assert box["width"] <= 2 and box["height"] <= 2
        guest.screenshot(path=str(output / ("guest-keyboard-" + engine + ".png")), full_page=True)
        with guest.expect_response(lambda response: response.url.endswith("/setup/api/input") and response.request.method == "POST"):
            guest.keyboard.insert_text("SYNTHETIC_MOBILE_INPUT")
        # Headless browsers do not show a real OS keyboard. Resize the viewport
        # to exercise the same proxy positioning path without claiming that test.
        guest.set_viewport_size({"width": 390, "height": 420})
        guest.wait_for_timeout(100)
        box = proxy.bounding_box()
        visible = guest.evaluate("({top:visualViewport.offsetTop, height:visualViewport.height})")
        assert visible["top"] <= box["y"] < visible["top"] + visible["height"]
        before = len(offline.calls)
        with guest.expect_response(lambda response: response.url.endswith("/setup/api/input") and response.request.method == "POST"):
            proxy.evaluate("""element => {
element.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
element.value = '日本語';
element.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', isComposing:true, bubbles:true, cancelable:true}));
element.dispatchEvent(new InputEvent('input', {data:'日本語', inputType:'insertCompositionText', isComposing:true, bubbles:true}));
element.dispatchEvent(new CompositionEvent('compositionend', {data:'日本語', bubbles:true}));
element.dispatchEvent(new InputEvent('input', {data:'日本語', inputType:'insertText', bubbles:true}));
}""")
        events = [event for _, action, payload in offline.calls[before:] if action == "input" for event in payload["events"]]
        assert events == [{"action": "text", "text": "日本語"}], "Composition was submitted early or duplicated"
        # Intentional page scrolling must remain possible while the proxy has focus.
        before_scroll = guest.evaluate("scrollY")
        guest.mouse.move(5, 200)
        guest.mouse.wheel(0, 70)
        guest.wait_for_function("before => scrollY > before + 10", arg=before_scroll)
        guest.locator("#remote-done").click()
        assert not proxy.evaluate("element => element === document.activeElement")
        guest.locator("#remote-show-input").click()
        assert guest.locator("#remote-show-input").get_attribute("aria-expanded") == "true"
        assert proxy.evaluate("element => getComputedStyle(element).opacity") == "1"
        with guest.expect_response(lambda response: response.url.endswith("/setup/api/input") and response.request.method == "POST"):
            proxy.fill("SYNTHETIC_PASTE_FALLBACK")
        guest.locator("#remote-show-input").click()
        assert guest.locator("#remote-show-input").get_attribute("aria-expanded") == "false"
        guest.set_viewport_size({"width": 390, "height": 844})
        assert not guest.evaluate("document.documentElement.scrollWidth > innerWidth")
        guest.locator("#cancel-setup").click()
        guest.locator("#setup-error").wait_for(state="visible")
        assert not errors, errors
    finally:
        context.close()
