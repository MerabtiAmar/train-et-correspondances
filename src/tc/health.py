"""Container healthcheck: `python -m tc.health` exits 1 if the heartbeat is older than 10 minutes.

The heartbeat proves the collection loop is alive. An empty feed is a normal state and does not
make the collector unhealthy.
"""

from __future__ import annotations

import json
import sys
import time

from tc.common.config import Settings, get_settings


def check(settings: Settings, now: float | None = None) -> tuple[bool, str]:
    now = time.time() if now is None else now
    try:
        beat = json.loads(settings.heartbeat_path.read_text(encoding="utf-8"))["ts"]
    except (OSError, ValueError, KeyError, TypeError):
        return False, f"no readable heartbeat at {settings.heartbeat_path}"
    age = now - beat
    if age > settings.health_max_age_s:
        return False, f"heartbeat is {int(age)} s old (limit {settings.health_max_age_s} s)"
    return True, f"ok, heartbeat {int(age)} s old"


def main() -> int:
    ok, message = check(get_settings())
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
