from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone

STATUS_BY_KIND = {
    "invite": "invited",
    "route": "routed_for_review",
    "identify": "identification_requested",
}

# Unambiguous alphabet for human-readable accept codes (no 0/O/1/I/L).
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

# Placeholder domain until the web app ships a real accept flow.
ACCEPT_URL_BASE = "https://pluribus.so/accept"


def generate_accept_code() -> str:
    segment = lambda: "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    return f"PL-{segment()}-{segment()}"


def record_action(
    path: str,
    kind: str,
    talent_id: str | None,
    name: str,
    source_key: str,
    email: str = "",
    note: str = "",
    delivery: str = "",
) -> dict:
    if kind not in STATUS_BY_KIND:
        raise ValueError(f"Unknown action kind: {kind}")

    records = read_actions(path)
    record = {
        "kind": kind,
        "status": STATUS_BY_KIND[kind],
        "talent_id": talent_id,
        "name": name,
        "source_key": source_key,
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if kind == "invite":
        code = generate_accept_code()
        record.update(
            {
                "email": email,
                "note": note,
                "delivery": delivery or "link",
                "accept_code": code,
                "accept_url": f"{ACCEPT_URL_BASE}/{code}",
            }
        )
    records.append(record)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2)
    return record


def read_actions(path: str | None) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
