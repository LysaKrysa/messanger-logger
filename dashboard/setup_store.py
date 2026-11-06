"""Single-use public onboarding invitations, separate from administrator sessions."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import uuid

from .store import DashboardError, account_id, digest

TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SESSION_SECONDS = 1200


class SetupInvites:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS setup_invites(
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                session_hash TEXT UNIQUE, session_expires REAL, csrf TEXT, name TEXT,
                account_id TEXT, telegram_link TEXT, claimed REAL
            )''')

    def create(self, label="", hours=24):
        if type(hours) is not int or hours not in {1, 6, 24}:
            raise DashboardError("Choose a link lifetime of 1, 6, or 24 hours.")
        label = label.strip() if isinstance(label, str) else ""
        if len(label) > 80 or any(ord(c) < 32 for c in label):
            raise DashboardError("Use a link label of at most 80 characters.")
        if not self.store.bot():
            raise DashboardError("Connect the shared Telegram bot before creating setup links.")
        ident, token, now = uuid.uuid4().hex, secrets.token_urlsafe(32), self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            accounts = db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            reserved = db.execute("SELECT COUNT(*) FROM setup_invites WHERE expires>? AND (state='pending' OR (state='claimed' AND session_expires>?))", (now, now)).fetchone()[0]
            if accounts + reserved >= self.store.max_accounts:
                raise DashboardError("All account slots are in use or reserved by active setup links. Disable an unused link first.")
            db.execute("INSERT INTO setup_invites(id,token_hash,label,created,expires) VALUES (?,?,?,?,?)", (ident, digest(token), label or "New invitation", now, now + hours * 3600))
        return ident, token

    def list(self):
        now = self.store.clock()
        with self.store.db() as db:
            db.execute("UPDATE setup_invites SET session_hash=NULL,csrf=NULL,telegram_link=NULL WHERE session_expires<=?", (now,))
            db.execute("DELETE FROM setup_invites WHERE expires<?", (now - 7 * 86400,))
            rows = [dict(row) for row in db.execute("SELECT id,label,created,expires,state,session_expires,account_id,claimed FROM setup_invites ORDER BY CASE WHEN expires>? AND (state='pending' OR (state='claimed' AND session_expires>?)) THEN 0 ELSE 1 END,created DESC,rowid DESC LIMIT 100", (now, now))]
        for row in rows:
            if row["state"] in {"pending", "claimed"} and (row["expires"] <= now or row["state"] == "claimed" and row["session_expires"] <= now):
                row["state"] = "expired"
            row["active"] = row["state"] in {"pending", "claimed"}
        return rows

    def claim(self, token, name):
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise DashboardError("Enter your name, using 1 to 80 characters.")
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise DashboardError("This invitation is unavailable, expired, or already used.")
        session, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,expires FROM setup_invites WHERE token_hash=? AND state='pending' AND expires>?", (digest(token), now)).fetchone()
            if not row:
                raise DashboardError("This invitation is unavailable, expired, or already used.")
            db.execute("UPDATE setup_invites SET state='claimed',session_hash=?,session_expires=?,csrf=?,name=?,claimed=? WHERE id=?", (digest(session), min(row["expires"], now + SESSION_SECONDS), csrf, name, now, row["id"]))
        return session

    def session(self, token, *, db=None):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        if db is None:
            with self.store.db() as connection:
                return self.session(token, db=connection)
        now = self.store.clock()
        row = db.execute("SELECT * FROM setup_invites WHERE session_hash=? AND state IN ('claimed','completed') AND expires>? AND session_expires>?", (digest(token), now, now)).fetchone()
        return dict(row) if row else None

    def complete(self, session, cookies):
        parsed = self.store.validate_cookies(json.dumps(cookies))
        ident, telegram_token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        bot = self.store.bot()
        if not bot:
            raise DashboardError("The shared Telegram bot is unavailable. Contact the administrator.")
        link = "https://t.me/" + bot["username"] + "?start=" + telegram_token
        try:
            with self.store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = self.session(session, db=db)
                if not row:
                    raise DashboardError("This invitation is no longer active.")
                if row["state"] == "completed":
                    return row["account_id"]
                if db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= self.store.max_accounts:
                    raise DashboardError("This server has reached its account limit. Contact the administrator.")
                db.execute("INSERT INTO accounts(id,name,cookies,facebook_id,created) VALUES (?,?,?,?,?)", (ident, row["name"], self.store.seal("cookies:" + ident, parsed), parsed["c_user"], self.store.clock()))
                db.execute("INSERT INTO invites VALUES (?,?,?)", (digest(telegram_token), ident, self.store.clock() + 900))
                db.execute("UPDATE setup_invites SET state='completed',account_id=?,telegram_link=? WHERE id=?", (ident, self.store.seal("setup-link:" + row["id"], link), row["id"]))
        except sqlite3.IntegrityError:
            raise DashboardError("This Messenger account is already connected. Contact the administrator instead of creating a duplicate.") from None
        return ident

    def progress(self, token):
        row = self.session(token)
        if not row:
            raise DashboardError("This setup session has expired or was disabled. Ask for a new invitation.")
        result = {"id": row["id"], "state": row["state"], "csrf": row["csrf"], "expires": row["session_expires"], "name": row["name"], "telegram_url": None, "pairing": "not_started"}
        if row["state"] == "completed":
            with self.store.db() as db:
                account = db.execute("SELECT chat_id,pending_chat FROM accounts WHERE id=?", (row["account_id"],)).fetchone()
                invitation = db.execute("SELECT 1 FROM invites WHERE account_id=? AND expires>?", (row["account_id"], self.store.clock())).fetchone()
            result["pairing"] = "approved" if account and account["chat_id"] else "pending_approval" if account and account["pending_chat"] else "waiting" if invitation else "expired"
            if invitation and row["telegram_link"]:
                result["telegram_url"] = self.store.unseal("setup-link:" + row["id"], row["telegram_link"])
        return result

    def revoke(self, ident):
        account_id(ident)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT account_id FROM setup_invites WHERE id=?", (ident,)).fetchone()
            if not row:
                raise DashboardError("Setup link not found.")
            db.execute("UPDATE setup_invites SET state='revoked',session_hash=NULL,csrf=NULL,telegram_link=NULL WHERE id=?", (ident,))
            if row["account_id"]:
                db.execute("DELETE FROM invites WHERE account_id=?", (row["account_id"],))
