"""Connection to the Pluribus webapp (device pairing + token storage).

This is the only module that talks to the network. Every function degrades
gracefully: if the server is unreachable the plugin keeps working locally and
the UI shows an offline state instead of an error. The API token is stored in
connection.json inside the plugin data dir; the pairing device code lives only
in memory, so a ComfyUI restart mid-pairing simply means starting over.
"""

from __future__ import annotations

import json
import os
import re
import socket
from typing import Any, Awaitable, Callable

from .storage import write_private_json

SERVER_URL = os.environ.get("PLURIBUS_SERVER_URL", "https://trypluribus.com").rstrip("/")

CONNECTION_FILENAME = "connection.json"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# One pairing in flight per ComfyUI process.
_pending: dict[str, Any] | None = None

# fetch(method, url, payload, token) -> (status_code, parsed_json)
Fetch = Callable[[str, str, dict | None, str | None], Awaitable[tuple[int, dict]]]


class RemoteUnavailable(Exception):
    """Server could not be reached; callers translate this to an offline state."""


async def _default_fetch(
    method: str, url: str, payload: dict | None = None, token: str | None = None
) -> tuple[int, dict]:
    import aiohttp

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, json=payload, headers=headers) as response:
                try:
                    data = await response.json()
                except Exception:
                    data = {}
                return response.status, data if isinstance(data, dict) else {}
    except Exception as exc:  # DNS, refused, timeout, TLS — all mean "offline".
        raise RemoteUnavailable(str(exc)) from exc


def _device_label() -> str:
    try:
        host = socket.gethostname()
    except OSError:
        host = "unknown host"
    return f"ComfyUI on {host}"[:80]


def read_connection(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) and data.get("token") else None
    except (OSError, json.JSONDecodeError):
        return None


def write_connection(path: str, connection: dict) -> None:
    write_private_json(path, connection)


def clear_connection(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def get_status(connection_path: str) -> dict:
    """Current connection state for the UI. Never touches the network."""
    connection = read_connection(connection_path)
    if connection:
        return {
            "state": "connected",
            "account_email": connection.get("account_email"),
            "server_url": connection.get("server_url", SERVER_URL),
        }
    if _pending:
        return {
            "state": "pairing",
            "user_code": _pending["user_code"],
            "verification_url": _pending["verification_url"],
            "interval": _pending["interval"],
            "server_url": SERVER_URL,
        }
    return {"state": "disconnected", "server_url": SERVER_URL}


async def start_pairing(fetch: Fetch | None = None) -> dict:
    """Ask the server for a pairing code the user approves at /pair."""
    global _pending
    fetch = fetch or _default_fetch
    try:
        status, data = await fetch(
            "POST", f"{SERVER_URL}/api/plugin/pair", {"deviceLabel": _device_label()}, None
        )
    except RemoteUnavailable:
        return {"state": "offline", "server_url": SERVER_URL}

    if status != 200 or not data.get("deviceCode"):
        return {
            "state": "error",
            "message": data.get("message") or f"Pairing failed ({status}).",
        }

    _pending = {
        "device_code": data["deviceCode"],
        "user_code": data.get("userCode", ""),
        "verification_url": data.get("verificationUrl", f"{SERVER_URL}/pair"),
        "interval": data.get("intervalSeconds", 5),
    }
    return {
        "state": "pairing",
        "user_code": _pending["user_code"],
        "verification_url": _pending["verification_url"],
        "interval": _pending["interval"],
    }


async def poll_pairing(connection_path: str, fetch: Fetch | None = None) -> dict:
    """One upstream poll. The UI drives timing by calling this repeatedly."""
    global _pending
    fetch = fetch or _default_fetch
    if not _pending:
        return get_status(connection_path)

    try:
        status, data = await fetch(
            "POST",
            f"{SERVER_URL}/api/plugin/pair/poll",
            {"deviceCode": _pending["device_code"]},
            None,
        )
    except RemoteUnavailable:
        # Keep the pairing alive; the user may fix their network and continue.
        return {"state": "offline", "user_code": _pending["user_code"]}

    if status == 429:
        return {"state": "pairing", "user_code": _pending["user_code"]}
    if status != 200:
        return {"state": "error", "message": data.get("message") or f"Poll failed ({status})."}

    result = data.get("status")
    if result == "pending":
        return {"state": "pairing", "user_code": _pending["user_code"]}

    if result == "approved" and data.get("token"):
        write_connection(
            connection_path,
            {
                "server_url": SERVER_URL,
                "token": data["token"],
                "account_email": data.get("accountEmail"),
                "paired_at": data.get("pairedAt") or _now_iso(),
            },
        )
        _pending = None
        return {"state": "connected", "account_email": data.get("accountEmail")}

    # denied / expired / not_found / consumed / revoked — pairing is dead.
    _pending = None
    return {"state": "failed", "reason": result or "unknown"}


async def push_invite(
    connection_path: str, invite: dict, fetch: Fetch | None = None
) -> dict | None:
    """Send an invite through the connected Pluribus account.

    Returns the server's canonical record (accept code/url, email delivery),
    ``unconfirmed`` when transport/5xx ambiguity means the server may have
    committed the request, ``unauthorized`` when the token was revoked, or
    None when the plugin simply isn't connected.
    """
    fetch = fetch or _default_fetch
    connection = read_connection(connection_path)
    if not connection:
        return None

    payload = {
        "name": invite.get("name") or "Unknown",
        "delivery": invite.get("delivery") or "email",
    }
    for local_key, remote_key in (
        ("email", "email"),
        ("note", "note"),
        ("source_key", "sourceKey"),
        ("source_kind", "sourceKind"),
        ("workflow_name", "workflowName"),
        ("workflow_fingerprint", "workflowFingerprint"),
        ("client_request_id", "clientRequestId"),
    ):
        value = invite.get(local_key)
        if value not in (None, ""):
            payload[remote_key] = value
    if invite.get("scope_statements"):
        payload["scopeStatements"] = invite["scope_statements"]
    if payload["delivery"] not in ("email", "link"):
        payload["delivery"] = "email"
    if payload["delivery"] == "email" and not _EMAIL_PATTERN.fullmatch(
        str(payload.get("email") or "")
    ):
        return {
            "state": "validation_error",
            "message": "Enter a valid email address for email delivery.",
        }

    try:
        status, data = await fetch(
            "POST",
            f"{connection.get('server_url', SERVER_URL)}/api/plugin/invites",
            payload,
            connection["token"],
        )
    except RemoteUnavailable:
        return {"state": "unconfirmed"}

    if status == 401:
        return {"state": "unauthorized"}
    if status >= 500:
        return {
            "state": "unconfirmed",
            "message": data.get("message") or f"Invite result was not confirmed ({status}).",
        }
    if status != 200:
        return {"state": "error", "message": data.get("message") or f"Invite failed ({status})."}

    record = data.get("invite")
    if not isinstance(record, dict) or not record.get("acceptCode"):
        return {
            "state": "unconfirmed",
            "message": "Pluribus returned an incomplete invite response.",
        }
    result = {
        "state": "sent",
        "accept_code": record.get("acceptCode"),
        "accept_url": record.get("acceptUrl"),
        "email_delivery": record.get("emailDelivery"),
        "status": record.get("status"),
        "invite_id": record.get("id"),
    }
    for remote_key, local_key in (
        ("emailAttemptState", "email_attempt_state"),
        ("emailAttemptStartedAt", "email_attempt_started_at"),
        ("emailReconciliationRequired", "email_reconciliation_required"),
    ):
        if remote_key in record:
            result[local_key] = record[remote_key]
    return result


async def fetch_invite_statuses(
    connection_path: str, fetch: Fetch | None = None
) -> dict:
    """Pull invite statuses from the server for local sync."""
    fetch = fetch or _default_fetch
    connection = read_connection(connection_path)
    if not connection:
        return {"state": "disconnected", "invites": []}

    try:
        status, data = await fetch(
            "GET",
            f"{connection.get('server_url', SERVER_URL)}/api/plugin/invites",
            None,
            connection["token"],
        )
    except RemoteUnavailable:
        return {"state": "offline", "invites": []}

    if status == 401:
        return {"state": "unauthorized", "invites": []}
    if status != 200:
        return {"state": "error", "invites": []}
    return {"state": "ok", "invites": data.get("invites") or []}


async def disconnect(connection_path: str, fetch: Fetch | None = None) -> dict:
    """Clear a token only after its server-side revocation is confirmed."""
    global _pending
    fetch = fetch or _default_fetch
    connection = read_connection(connection_path)
    if not connection:
        _pending = None
        return {"state": "disconnected", "server_url": SERVER_URL}

    try:
        status, data = await fetch(
            "POST",
            f"{connection.get('server_url', SERVER_URL)}/api/plugin/revoke",
            None,
            connection["token"],
        )
    except RemoteUnavailable:
        return {
            "state": "offline",
            "message": "Revocation could not be confirmed; the local token was kept for retry.",
        }

    # Any 2xx confirms the revoke endpoint completed. A 401 also proves this
    # token can no longer authorize API calls, so it is safe to forget locally.
    if not (200 <= status < 300 or status == 401):
        return {
            "state": "error",
            "message": data.get("message")
            or "Revocation could not be confirmed; the local token was kept for retry.",
        }

    clear_connection(connection_path)
    _pending = None
    return {"state": "disconnected", "server_url": SERVER_URL}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def reset_pending_for_tests() -> None:
    global _pending
    _pending = None
