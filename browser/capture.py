"""Disposable viewport-capture sessions, independent of login/session cookies."""


class FrameCapture:
    def __init__(self, context):
        self.context = context
        self.page = self.session = None

    def reset(self):
        session = self.session
        self.page = self.session = None
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass

    def jpeg(self, page):
        try:
            if self.page is not page:
                self.reset()
                self.session = self.context.new_cdp_session(page)
                self.page = page
            result = self.session.send("Page.captureScreenshot", {
                "format": "jpeg", "quality": 65, "fromSurface": True,
                "captureBeyondViewport": False, "optimizeForSpeed": True,
            })["data"]
            if not isinstance(result, str) or not result:
                raise ValueError("Frame unavailable.")
            return result
        except Exception:
            # A redirect/detached target must not poison every later frame.
            # Only capture is retried by a later read; no input is replayed.
            self.reset()
            raise
