"""Parsing of the X-Simulate-Failure test header.

Format: comma-separated <key_id>=<status> pairs, where key_id is key_1, key_2, ... or `*`
for every key, and status is one of 429, 500, 502, 503. Malformed pairs are ignored.
"""

ALLOWED_STATUSES = {429, 500, 502, 503}


def parse_simulation_header(value: str | None) -> dict[str, int]:
    failures: dict[str, int] = {}
    if not value:
        return failures
    for pair in value.split(","):
        key_id, sep, status = pair.partition("=")
        key_id, status = key_id.strip(), status.strip()
        if not sep or not key_id or not status.isdigit():
            continue
        code = int(status)
        if code in ALLOWED_STATUSES:
            failures[key_id] = code
    return failures


def status_for(failures: dict[str, int], key_id: str) -> int | None:
    """The simulated status for a key: an explicit entry wins over `*`."""
    return failures.get(key_id, failures.get("*"))
