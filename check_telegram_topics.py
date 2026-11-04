"""Fresh read-only Telegram topic capability check; no messages or local writes."""
from __future__ import annotations

import re

from telegram_relay import TelegramRelay, TelegramConfigurationError, boolean
from telegram_topics import TopicRouter, TopicError, TelegramRejected

READ_METHODS = frozenset({"getMe", "getChat", "getChatMember"})


def check():
    enabled = boolean("TELEGRAM_ENABLED", False)
    topics = boolean("TELEGRAM_TOPICS", False)
    print("Fresh read-only Telegram topic check (no cached result).")
    print("Logger forwarding:", "enabled" if enabled else "disabled")
    print("Logger conversation topics:", "enabled" if topics else "disabled")
    if not enabled:
        print("Set TELEGRAM_ENABLED=true in messenger.env, then restart the logger.")
        print("Telegram API check skipped; no requests were made.")
        return 2
    if not topics:
        print("Set TELEGRAM_TOPICS=true in messenger.env, then restart the logger.")
    relay = TelegramRelay(lambda message: None, None, start=False)
    relay.interval = 0

    def api(method, params):
        if method not in READ_METHODS:
            raise RuntimeError("This checker permits only read-only Telegram methods.")
        try:
            result = relay._api(method, params)
        except TelegramRejected as exc:
            print("Telegram read failed:", method, "HTTP", exc.code)
            raise
        if method == "getMe" and isinstance(result, dict):
            username = result.get("username")
            if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,64}", username):
                print("Configured bot: @" + username)
            value = result.get("has_topics_enabled")
            label = "enabled" if value is True else ("disabled" if value is False else "not reported (treated as disabled)")
            print("Telegram bot Threaded mode:", label)
        elif method == "getChat" and isinstance(result, dict):
            kind = result.get("type")
            print("Destination type:", kind if kind in {"private", "group", "supergroup", "channel"} else "unrecognized")
            if kind == "supergroup":
                print("Group Topics:", "enabled" if result.get("is_forum") is True else "disabled")
        return result

    router = TopicRouter(api, None, relay.token.split(":", 1)[0], relay.chat)
    try:
        router.destination()
    except TopicError as exc:
        print("Topic setup:", str(exc))
        return 2
    print("Topic capability and destination: ready.")
    print("No messages were sent, no topics were created, and no local data was changed.")
    if topics:
        print("Restart the logger to reload its source/settings, then test a fresh event eligible for forwarding.")
    return 0 if topics else 2


def main():
    try:
        return check()
    except TelegramConfigurationError as exc:
        print("Telegram configuration:", str(exc))
        return 2
    except Exception:
        print("Telegram topic check failed; private values are hidden.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
