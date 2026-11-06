"""Bounded, ordered viewer inputs; shared by the web gateway and browser agent."""
import json
import math

KEYS = frozenset({"Backspace", "Delete", "Enter", "Tab", "Shift+Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown", "Control+A"})


def number(value):
    if type(value) not in {int, float} or not math.isfinite(value) or abs(value) > 1000000:
        raise ValueError("Invalid input coordinates.")
    return value


def normalize(events):
    if not isinstance(events, list) or not 1 <= len(events) <= 24:
        raise ValueError("Input batch must contain 1 to 24 events.")
    result = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid browser input.")
        action = event.get("action")
        item = {"action": action}
        if action == "click":
            item.update(x=number(event.get("x")), y=number(event.get("y")))
        elif action == "scroll":
            item["delta"] = max(-1536, min(1536, number(event.get("delta"))))
        elif action == "key" and isinstance(event.get("key"), str) and event["key"] in KEYS:
            item["key"] = event["key"]
        elif action == "text" and isinstance(event.get("text"), str) and 0 < len(event["text"]) <= 2000:
            item["text"] = event["text"]
        elif action != "reload":
            raise ValueError("Unsupported browser input.")
        result.append(item)
    if len(json.dumps(result, ensure_ascii=False).encode()) > 14000:
        raise ValueError("Browser input batch is too large.")
    return result
