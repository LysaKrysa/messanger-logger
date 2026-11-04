"""Consistent display labels; placeholders are never treated as real names."""
import re

PLACEHOLDERS = {"username", "user name", "your username", "your name", "name", "user", "unknown", "unknown user", "unknown participant", "facebook user", "messenger user", "none", "null"}


def display_label(value):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split()).strip()[:200]
    if value.casefold().strip("<>@_") in PLACEHOLDERS or value.isdecimal():
        return ""
    return value


def username_label(value):
    if not isinstance(value, str):
        return ""
    value = value.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value) or not display_label(value):
        return ""
    return value


def display_name(name, username="", *, own=False):
    name, username = display_label(name), username_label(username)
    if name:
        return name + (" (@" + username + ")" if username else "")
    return "@" + username if username else ("You" if own else "Unknown participant")
