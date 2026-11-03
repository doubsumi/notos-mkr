import json
import time


def save(events: list[dict], path: str) -> None:
    payload = {
        "version": 1,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": events[-1]["t"] if events else 0.0,
        "events": events,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def load(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("events", [])