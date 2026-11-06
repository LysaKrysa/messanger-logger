"""Public guest flow with its own cookie, CSRF boundary and private controller client."""
from __future__ import annotations

import hmac
import json
import re
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, url_for

from .setup_store import SetupInvites, SESSION_SECONDS
from .store import DashboardError

COOKIE = "__Host-messenger_setup"


class BrowserClient:
    def __init__(self, secret):
        self.secret = secret

    def call(self, ident, action, data=None):
        if not re.fullmatch(r"[a-f0-9]{32}", ident) or action not in {"start", "stop", "frame", "click", "key", "text", "scroll", "reload", "capture"}:
            raise DashboardError("Invalid browser request.")
        try:
            req = Request("http://127.0.0.1:8789/browser/" + ident + "/" + action,
                data=json.dumps(data or {}).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.secret})
            try:
                response = urlopen(req, timeout=45 if action in {"start", "reload"} else 15)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(1500001)
                if len(raw) > 1500000:
                    raise ValueError
                result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            if "error" in result:
                # Controller uses fixed errors only; do not relay arbitrary HTTP bodies.
                if result["error"] == "Someone else is connecting. Please try again shortly; one browser runs at a time.":
                    raise DashboardError(result["error"])
                raise DashboardError("The temporary browser is unavailable or the action could not finish. Complete Facebook's checks, or restart the browser.")
            return result
        except DashboardError:
            raise
        except Exception:
            raise DashboardError("The temporary browser could not be reached. Try again shortly or contact the administrator.") from None


def install(app, store, origin, browser=None):
    invitations = SetupInvites(store)
    browser = browser or BrowserClient(store.secret("remote-browser-api"))
    app.extensions["setup_invites"] = invitations
    app.extensions["setup_browser"] = browser
    guest = Blueprint("setup", __name__)

    def guest_session():
        token = request.cookies.get(COOKIE)
        row = invitations.session(token)
        if not row:
            abort(401)
        if not store.take_limit("setup-actions:" + row["id"], 600, 60):
            abort(429)
        if request.method == "POST":
            supplied = request.headers.get("X-Setup-CSRF", "")
            if not supplied.isascii() or not hmac.compare_digest(supplied, row["csrf"]) or request.headers.get("Origin") != origin:
                abort(403)
        return token, row

    def live_session():
        token, row = guest_session()
        if row["state"] != "claimed":
            raise DashboardError("Facebook setup is already complete. Continue to Telegram.")
        return token, row

    @guest.get("/setup")
    def page():
        return render_template("setup.html")

    @guest.post("/setup/api/claim")
    def claim():
        if request.headers.get("Origin") != origin:
            abort(403)
        if not store.take_limit("setup-claim:" + store.secret(request.remote_addr or "unknown"), 12, 900):
            abort(429)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or data.get("consent") is not True:
            raise DashboardError("Please read and accept how the temporary browser and session will be used.")
        token = invitations.claim(data.get("token"), data.get("name"))
        response = jsonify(invitations.progress(token))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="Strict", path="/")
        return response

    @guest.get("/setup/api/status")
    def status():
        token, _ = guest_session()
        return jsonify(invitations.progress(token))

    @guest.post("/setup/api/start")
    def start():
        _, row = live_session()
        data = request.get_json(silent=True) or {}
        browser.call(row["id"], "start", {"expires": row["session_expires"], "mobile": isinstance(data, dict) and data.get("mobile") is True})
        return jsonify(started=True)

    @guest.post("/setup/api/frame")
    def frame():
        _, row = live_session()
        result = browser.call(row["id"], "frame")
        # Only pixels, the observed hostname and a readiness flag reach the guest.
        return jsonify(jpeg=result.get("jpeg", ""), site=result.get("site", "Starting browser"), width=result.get("width", 1024), height=result.get("height", 768), ready=result.get("ready") is True)

    @guest.post("/setup/api/input")
    def action():
        _, row = live_session()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400)
        action = data.get("action")
        if action not in {"click", "key", "text", "scroll", "reload"}:
            abort(400)
        browser.call(row["id"], action, {k: v for k, v in data.items() if k in {"x", "y", "key", "text", "delta"}})
        return jsonify(ok=True)

    @guest.post("/setup/api/finish")
    def finish():
        token, row = guest_session()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or data.get("approve") is not True:
            raise DashboardError("Approve connecting this Facebook session before continuing.")
        if row["state"] == "claimed":
            captured = browser.call(row["id"], "capture")
            invitations.complete(token, captured.get("cookies"))
            try:
                browser.call(row["id"], "stop")
            except DashboardError:
                pass  # Host's independent hard deadline still closes the sandbox.
        return jsonify(invitations.progress(token))

    @guest.post("/setup/api/cancel")
    def cancel():
        _, row = guest_session()
        invitations.revoke(row["id"])
        try:
            browser.call(row["id"], "stop")
        except DashboardError:
            pass
        response = jsonify(cancelled=True)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="Strict", path="/")
        return response

    @guest.errorhandler(Exception)
    def guest_error(error):
        code = getattr(error, "code", 400)
        message = str(error) if isinstance(error, DashboardError) else {
            401: "This setup session has expired or was disabled. Ask for a new invitation.",
            403: "This request could not be verified. Reload the invitation page.",
            429: "Please slow down and try again shortly.",
        }.get(code, "Setup could not continue. No private details were recorded.")
        return jsonify(error=message), code

    app.register_blueprint(guest)

    @app.template_filter("setup_time")
    def setup_time(value):
        return datetime.fromtimestamp(value, ZoneInfo("Europe/Warsaw")).strftime("%d %b · %H:%M %Z") if value else "—"

    @app.route("/setup-links", methods=["GET", "POST"])
    def setup_links():
        link = None
        if request.method == "POST":
            try:
                hours = int(request.form.get("hours", "24"))
            except ValueError:
                raise DashboardError("Choose a valid link lifetime.") from None
            _, token = invitations.create(request.form.get("label", ""), hours)
            link = origin + "/setup#" + token
        rows = invitations.list()
        return render_template("setup_links.html", links=rows, new_link=link, active=sum(row["active"] for row in rows))

    @app.post("/setup-links/<ident>/disable")
    def setup_disable(ident):
        invitations.revoke(ident)
        try:
            browser.call(ident, "stop")
        except DashboardError:
            pass
        return redirect(url_for("setup_links"))
