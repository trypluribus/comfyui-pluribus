"""Connection to the Pluribus webapp (device pairing + token storage).

This is the only module that talks to the network. Every function degrades
gracefully: if the server is unreachable the plugin keeps working locally and
the UI shows an offline state instead of an error. The API token is stored in
connection.json inside the plugin data dir; the pairing device code lives only
in memory, so a ComfyUI restart mid-pairing simply means starting over.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from typing import Any, Awaitable, Callable

from .bindings import SHA256_PATTERN, normalize_source_links

from .storage import write_private_json

SERVER_URL = os.environ.get("PLURIBUS_SERVER_URL", "https://trypluribus.com").rstrip("/")

CONNECTION_FILENAME = "connection.json"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_REMOTE_REQUEST_TIMEOUT_SECONDS = 30

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
    # A production workflow write can legitimately take longer than ten seconds
    # while the API completes its related persistence work. Keep the request
    # bounded, but leave enough headroom to receive the committed response
    # instead of incorrectly reporting the server as offline.
    timeout = aiohttp.ClientTimeout(total=_REMOTE_REQUEST_TIMEOUT_SECONDS)
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
    # Hostnames often contain a person's name, company, or internal network
    # convention. Pairing only needs a useful product label.
    return "ComfyUI plugin"


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


async def _proxy_json(
    connection_path: str,
    method: str,
    path: str,
    payload: dict | None = None,
    fetch: Fetch | None = None,
) -> tuple[int, dict]:
    """Make one authenticated request while preserving upstream JSON/status."""
    fetch = fetch or _default_fetch
    connection = read_connection(connection_path)
    if not connection:
        return 401, {
            "state": "disconnected",
            "message": "Connect this ComfyUI plugin to Pluribus first.",
        }
    try:
        status, data = await fetch(
            method,
            f"{connection.get('server_url', SERVER_URL)}{path}",
            payload,
            connection["token"],
        )
    except RemoteUnavailable:
        return 503, {
            "state": "offline",
            "message": "Pluribus could not be reached. Your local workflow was not changed.",
        }
    return status, data if isinstance(data, dict) else {}


def _pick(body: object, fields: tuple[str, ...]) -> dict:
    if not isinstance(body, dict):
        raise ValueError("Request body must be an object.")
    return {field: body[field] for field in fields if body.get(field) not in (None, "")}


_FORBIDDEN_REMOTE_KEYS = {
    "filename",
    "filepath",
    "graph",
    "localworkflowkey",
    "nodeid",
    "nodes",
    "outputnodeid",
    "prompt",
    "provenance",
    "sourcekey",
    "sourcenodeid",
    "sourcepath",
    "workflowfilename",
    "workflowfingerprint",
    "workflowjson",
    "workflowname",
}
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


def _opaque_id(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not _OPAQUE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be an opaque identifier.")
    return normalized


def _assert_no_graph_material(value: object) -> None:
    """Reject accidental graph/source leakage in otherwise structured bodies."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in _FORBIDDEN_REMOTE_KEYS:
                raise ValueError(f"{key} is local-only and cannot be sent to Pluribus.")
            _assert_no_graph_material(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_graph_material(child)


async def fetch_workspace(
    connection_path: str, fetch: Fetch | None = None
) -> tuple[int, dict]:
    return await _proxy_json(connection_path, "GET", "/api/plugin/workspace", fetch=fetch)


async def create_workspace(
    connection_path: str, body: dict, fetch: Fetch | None = None
) -> tuple[int, dict]:
    payload = _pick(body, ("organizationName", "licenseeType"))
    return await _proxy_json(
        connection_path, "POST", "/api/plugin/workspace", payload, fetch
    )


async def fetch_projects(
    connection_path: str, fetch: Fetch | None = None
) -> tuple[int, dict]:
    return await _proxy_json(connection_path, "GET", "/api/plugin/projects", fetch=fetch)


async def create_project(
    connection_path: str, body: dict, fetch: Fetch | None = None
) -> tuple[int, dict]:
    payload = _pick(body, ("title", "clientName", "agencyName", "description"))
    return await _proxy_json(
        connection_path, "POST", "/api/plugin/projects", payload, fetch
    )


async def fetch_project(
    connection_path: str,
    project_id: str,
    workflow_ref: str | None = None,
    fetch: Fetch | None = None,
) -> tuple[int, dict]:
    project_id = _opaque_id(project_id, "projectId")
    suffix = ""
    if workflow_ref:
        try:
            workflow_ref = str(uuid.UUID(str(workflow_ref)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("workflowRef must be a UUID.") from exc
        suffix = f"?workflowRef={workflow_ref}"
    return await _proxy_json(
        connection_path,
        "GET",
        f"/api/plugin/projects/{project_id}{suffix}",
        fetch=fetch,
    )


def normalize_project_person_payload(body: object) -> dict[str, Any]:
    """Return the exact allow-listed body sent to the hosted people route."""

    payload = _pick(
        body,
        (
            "mode",
            "displayName",
            "talentRecordId",
            "talentEmail",
            "role",
            "clientPersonId",
        ),
    )
    representative = body.get("representative") if isinstance(body, dict) else None
    if representative not in (None, ""):
        payload["representative"] = _pick(representative, ("role", "name", "email"))
    if payload.get("talentRecordId"):
        payload["talentRecordId"] = _opaque_id(
            payload["talentRecordId"], "talentRecordId"
        )
    if payload.get("clientPersonId"):
        try:
            payload["clientPersonId"] = str(uuid.UUID(str(payload["clientPersonId"])))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("clientPersonId must be a UUID.") from exc
    return payload


def project_person_request_hash(body: object) -> str:
    payload = normalize_project_person_payload(body)
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def create_project_person(
    connection_path: str,
    project_id: str,
    body: dict,
    fetch: Fetch | None = None,
) -> tuple[int, dict]:
    project_id = _opaque_id(project_id, "projectId")
    payload = normalize_project_person_payload(body)
    return await _proxy_json(
        connection_path,
        "POST",
        f"/api/plugin/projects/{project_id}/people",
        payload,
        fetch,
    )


async def update_project_person(
    connection_path: str,
    project_id: str,
    person_id: str,
    body: dict,
    fetch: Fetch | None = None,
) -> tuple[int, dict]:
    project_id = _opaque_id(project_id, "projectId")
    person_id = _opaque_id(person_id, "personId")
    payload = _pick(body, ("displayName", "role"))
    representative = body.get("representative") if isinstance(body, dict) else None
    if representative not in (None, ""):
        payload["representative"] = _pick(
            representative, ("role", "name", "email")
        )
    _assert_no_graph_material(payload)
    return await _proxy_json(
        connection_path,
        "PATCH",
        f"/api/plugin/projects/{project_id}/people/{person_id}",
        payload,
        fetch,
    )


async def put_project_source_links(
    connection_path: str,
    project_id: str,
    body: dict,
    fetch: Fetch | None = None,
) -> tuple[int, dict]:
    project_id = _opaque_id(project_id, "projectId")
    # Re-normalize even when the local binding store already built this body.
    # This is the final network boundary and must remain independently safe.
    payload = normalize_source_links(
        workflow_ref=body.get("workflowRef") if isinstance(body, dict) else None,
        workflow_kind=body.get("workflowKind") if isinstance(body, dict) else None,
        graph_hash=body.get("graphHash") if isinstance(body, dict) else None,
        sources=body.get("sources") if isinstance(body, dict) else None,
        identity_review_hash=(
            body.get("identityReviewHash") if isinstance(body, dict) else None
        ),
        identity_revision=(
            body.get("identityRevision") if isinstance(body, dict) else None
        ),
    )
    supplied_manifest = str(body.get("manifestHash") or "")
    if supplied_manifest and supplied_manifest != payload["manifestHash"]:
        raise ValueError("manifestHash does not match the normalized rights manifest.")
    base_manifest_version = body.get("baseManifestVersion")
    if (
        isinstance(base_manifest_version, bool)
        or not isinstance(base_manifest_version, int)
        or base_manifest_version < 0
    ):
        raise ValueError("baseManifestVersion must be a non-negative integer.")
    payload["baseManifestVersion"] = base_manifest_version
    return await _proxy_json(
        connection_path,
        "PUT",
        f"/api/plugin/projects/{project_id}/source-links",
        payload,
        fetch,
    )


async def put_project_use(
    connection_path: str,
    project_id: str,
    body: dict,
    fetch: Fetch | None = None,
) -> tuple[int, dict]:
    project_id = _opaque_id(project_id, "projectId")
    if not isinstance(body, dict):
        raise ValueError("Request body must be an object.")
    _assert_no_graph_material(body)
    people = body.get("people", [])
    ai_actions = body.get("aiActions", [])
    try:
        workflow_ref = str(uuid.UUID(str(body.get("workflowRef") or "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("workflowRef must be a UUID.") from exc
    manifest_hash = str(body.get("rightsManifestHash") or "").lower()
    if not isinstance(people, list):
        raise ValueError("people must be a list.")
    if not isinstance(ai_actions, list):
        raise ValueError("aiActions must be a list.")
    if not SHA256_PATTERN.fullmatch(manifest_hash):
        raise ValueError("rightsManifestHash must be a SHA-256 hex digest.")
    scalar_fields = (
        "usageType",
        "deliverables",
        "channels",
        "platforms",
        "territories",
        "languages",
        "paidMediaAllowed",
        "organicMediaAllowed",
        "usageWindowStart",
        "usageWindowEnd",
        "productCategory",
        "finalCreativeApprovalRequired",
        "compensationHandling",
        "compensation",
        "exclusivityHandling",
        "exclusivity",
        "revocationInstructions",
        "takedownSla",
        "modelDisableRequired",
        "platformRemovalRequired",
    )
    payload = {
        field: deepcopy(body[field]) for field in scalar_fields if field in body
    }
    safe_actions = []
    for item in ai_actions:
        if not isinstance(item, dict):
            raise ValueError("Each aiActions entry must be an object.")
        safe_item = {
            "talentRecordId": _opaque_id(
                item.get("talentRecordId"), "talentRecordId"
            )
        }
        for field in ("modality", "action", "requiresFinalApproval", "notes"):
            if field in item:
                safe_item[field] = deepcopy(item[field])
        safe_actions.append(safe_item)
    safe_people = []
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("Each people entry must be an object.")
        safe_person = {
            "talentRecordId": _opaque_id(person.get("talentRecordId"), "talentRecordId"),
        }
        for field in (
            "compensation",
            "usageComfort",
            "restrictions",
            "representativeAuthority",
        ):
            if field in person:
                safe_person[field] = deepcopy(person[field])
        safe_people.append(safe_person)
    payload.update(
        {
            "workflowRef": workflow_ref,
            "rightsManifestHash": manifest_hash,
            "aiActions": safe_actions,
            "people": safe_people,
        }
    )
    return await _proxy_json(
        connection_path,
        "PUT",
        f"/api/plugin/projects/{project_id}/use",
        payload,
        fetch,
    )


async def create_confirmation_request(
    connection_path: str,
    project_id: str,
    body: dict,
    fetch: Fetch | None = None,
) -> tuple[int, dict]:
    project_id = _opaque_id(project_id, "projectId")
    payload = _pick(
        body,
        (
            "clientRequestId",
            "workflowRef",
            "rightsManifestHash",
            "talentRecordId",
            "recipientEmail",
            "recipientName",
            "recipientRole",
            "message",
            "delivery",
            "expiresInDays",
        ),
    )
    try:
        payload["clientRequestId"] = str(uuid.UUID(str(payload.get("clientRequestId") or "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("clientRequestId must be a UUID.") from exc
    try:
        payload["workflowRef"] = str(uuid.UUID(str(payload.get("workflowRef") or "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("workflowRef must be a UUID.") from exc
    manifest_hash = str(payload.get("rightsManifestHash") or "").lower()
    if not SHA256_PATTERN.fullmatch(manifest_hash):
        raise ValueError("rightsManifestHash must be a SHA-256 hex digest.")
    payload["rightsManifestHash"] = manifest_hash
    if payload.get("talentRecordId"):
        payload["talentRecordId"] = _opaque_id(
            payload["talentRecordId"], "talentRecordId"
        )
    return await _proxy_json(
        connection_path,
        "POST",
        f"/api/plugin/projects/{project_id}/confirmation-requests",
        payload,
        fetch,
    )


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
