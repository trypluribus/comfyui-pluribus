from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from .storage import write_private_json

STATUS_BY_KIND = {
    "invite": "draft",
    "route": "routed_for_review",
    "identify": "identification_requested",
}

# Connected invites use the server's canonical code. This base is only a
# fallback for older servers that return a code without the full URL; local
# drafts never receive a code or hosted URL.
ACCEPT_URL_BASE = os.environ.get(
    "PLURIBUS_ACCEPT_URL_BASE", "https://trypluribus.com/accept"
)


def record_action(
    path: str,
    kind: str,
    talent_id: str | None,
    name: str,
    source_key: str,
    source_kind: str = "",
    workflow_name: str = "",
    workflow_fingerprint: str = "",
    scope_statements: list[str] | None = None,
    email: str = "",
    note: str = "",
    delivery: str = "",
    override: dict | None = None,
    draft_reason: str = "",
    client_request_id: str = "",
    delivery_result: dict | None = None,
) -> dict:
    """Record an action locally.

    Invites become ``invited`` only when ``override`` contains the connected
    server's canonical accept code. Every other invite result is a local draft
    with no code or hosted URL, so an offline/error path cannot create a dead
    bearer link or look delivered in the audit trail.
    """
    if kind not in STATUS_BY_KIND:
        raise ValueError(f"Unknown action kind: {kind}")

    records = read_actions(path)
    scope_statements = normalize_scope_statements(scope_statements)
    if kind == "invite" and not client_request_id:
        client_request_id = client_request_id_for_invite(
            path,
            {
                "talent_id": talent_id,
                "name": name,
                "source_key": source_key,
                "source_kind": source_kind,
                "workflow_name": workflow_name,
                "workflow_fingerprint": workflow_fingerprint,
                "scope_statements": scope_statements,
                "email": email,
                "note": note,
                "delivery": delivery,
            },
        )

    existing_index = None
    if kind == "invite" and client_request_id:
        existing_index = next(
            (
                index
                for index, existing in enumerate(records)
                if existing.get("kind") == "invite"
                and existing.get("client_request_id") == client_request_id
            ),
            None,
        )

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if (
        kind == "invite"
        and existing_index is not None
        and not (override and override.get("accept_code"))
        and records[existing_index].get("accept_code")
    ):
        # A later transport/auth failure cannot erase a canonical link that a
        # prior response already confirmed. Keep the exact server record and
        # its idempotency key so a reopened dialog can retry/sync safely.
        preserved = dict(records[existing_index])
        preserved.update(_email_attempt_fields(delivery_result))
        preserved["updated_at"] = now
        if draft_reason:
            preserved["last_retry_state"] = draft_reason
        records[existing_index] = preserved
        write_private_json(path, records)
        return preserved

    if (
        kind == "invite"
        and existing_index is not None
        and not (override and override.get("accept_code"))
        and records[existing_index].get("draft_reason") == "unconfirmed"
    ):
        # A later definite local/HTTP failure cannot prove that an earlier
        # response-loss attempt did not commit. Keep the conservative state
        # and stable UUID until canonical sync or replay resolves it.
        preserved = dict(records[existing_index])
        preserved.update(_email_attempt_fields(delivery_result))
        preserved["updated_at"] = now
        if draft_reason:
            preserved["last_retry_state"] = draft_reason
        records[existing_index] = preserved
        write_private_json(path, records)
        return preserved

    recorded_at = (
        records[existing_index].get("recorded_at", now)
        if existing_index is not None
        else now
    )
    record = {
        "kind": kind,
        "status": STATUS_BY_KIND[kind],
        "talent_id": talent_id,
        "name": name,
        "source_key": source_key,
        "recorded_at": recorded_at,
    }
    if kind == "invite":
        record.update(
            {
                "source_kind": source_kind,
                "workflow_name": workflow_name,
                "workflow_fingerprint": workflow_fingerprint,
                "scope_statements": scope_statements,
                "client_request_id": client_request_id,
            }
        )
        if override and override.get("accept_code"):
            record.update(
                {
                    "status": "invited",
                    "email": email,
                    "note": note,
                    "delivery": delivery or "email",
                    "accept_code": override["accept_code"],
                    "accept_url": override.get("accept_url")
                    or f"{ACCEPT_URL_BASE}/{override['accept_code']}",
                    "synced": True,
                    "email_delivery": override.get("email_delivery", "skipped"),
                    "server_status": override.get("status", "sent"),
                    "server_invite_id": override.get("invite_id"),
                }
            )
        else:
            record.update(
                {
                    "email": email,
                    "note": note,
                    "requested_delivery": delivery or "email",
                    "synced": False,
                    "draft_reason": draft_reason or "disconnected",
                }
            )
        record.update(_email_attempt_fields(delivery_result or override))
    if existing_index is None:
        records.append(record)
    else:
        record["updated_at"] = now
        records[existing_index] = record

    write_private_json(path, records)
    return record


def normalize_scope_statements(scope_statements: list[str] | None) -> list[str]:
    if not isinstance(scope_statements, list):
        return []
    return [
        statement
        for item in scope_statements
        if (statement := str(item).strip())
    ]


def _email_attempt_fields(result: dict | None) -> dict:
    """Return normalized local email-attempt fields from a remote result."""
    if not result:
        return {}

    fields = {}
    for key in (
        "email_attempt_state",
        "email_attempt_started_at",
        "email_reconciliation_required",
    ):
        if key in result:
            fields[key] = result[key]

    if "email_reconciliation_required" in fields:
        fields["email_reconciliation_required"] = bool(
            fields["email_reconciliation_required"]
        )
    elif "email_attempt_state" in fields:
        fields["email_reconciliation_required"] = (
            fields["email_attempt_state"] == "manual_reconciliation"
        )
    return fields


def _request_identity(invite: dict) -> dict:
    return {
        "talent_id": invite.get("talent_id"),
        "name": str(invite.get("name") or "Unknown"),
        "source_key": str(invite.get("source_key") or ""),
        "source_kind": str(invite.get("source_kind") or ""),
        "workflow_name": str(invite.get("workflow_name") or ""),
        "workflow_fingerprint": str(invite.get("workflow_fingerprint") or ""),
        "email": str(invite.get("email") or "").strip().lower(),
        "note": str(invite.get("note") or ""),
        "delivery": str(
            invite.get("requested_delivery") or invite.get("delivery") or "email"
        ),
        "scope_statements": normalize_scope_statements(invite.get("scope_statements")),
    }


def client_request_id_for_invite(path: str, invite: dict) -> str:
    """Select one stable UUID for an invite request.

    A matching persisted draft or provider-ambiguous canonical invite wins
    over a newly supplied browser UUID so a retry after closing or reloading
    the dialog cannot create a duplicate. First attempts preserve a valid
    browser UUID; callers without one receive a server-minted UUID.
    """
    request_identity = _request_identity(invite)
    for record in reversed(read_actions(path)):
        retryable_canonical_invite = (
            record.get("status") == "invited"
            and (
                record.get("email_attempt_state")
                in {"ambiguous", "in_flight", "manual_reconciliation"}
                or record.get("email_reconciliation_required") is True
            )
        )
        if (
            record.get("kind") == "invite"
            and (record.get("status") == "draft" or retryable_canonical_invite)
            and record.get("client_request_id")
            and _request_identity(record) == request_identity
        ):
            return str(record["client_request_id"])

    provided = invite.get("client_request_id")
    if isinstance(provided, str):
        provided = provided.strip()
        try:
            parsed = uuid.UUID(provided)
        except ValueError:
            pass
        else:
            if str(parsed) == provided.lower():
                return provided

    return str(uuid.uuid4())


def latest_accepted_invite(
    actions: list[dict],
    *,
    workflow_name: str,
    workflow_fingerprint: str,
    source_key: str,
    source_kind: str,
    scope_statements: list[str],
    talent_id: str | None = None,
) -> dict | None:
    """Return the newest accepted server invite for this scanned person.

    Source keys are the narrowest identity available in a scan. Only fall back
    to ``talent_id`` when either side lacks a source key; this avoids applying
    acceptance for one asset to a different asset merely because both belong
    to the same roster person.
    """
    for record in reversed(actions):
        if (
            record.get("kind") != "invite"
            or record.get("synced") is not True
            or record.get("server_status") != "accepted"
        ):
            continue

        if str(record.get("workflow_name") or "") != workflow_name:
            continue
        if str(record.get("workflow_fingerprint") or "") != workflow_fingerprint:
            continue
        if str(record.get("source_kind") or "") != source_kind:
            continue
        if normalize_scope_statements(record.get("scope_statements")) != scope_statements:
            continue

        record_source_key = str(record.get("source_key") or "")
        if source_key and record_source_key:
            if record_source_key == source_key:
                return record
            continue

        if talent_id and record.get("talent_id") == talent_id:
            return record
    return None


def apply_status_updates(path: str, server_invites: list[dict]) -> int:
    """Merge server invite statuses by accept code or idempotency key.

    A transport failure can leave a linkless local draft after the server has
    committed the invite. In that case the next sync uses ``clientRequestId``
    to hydrate the same draft with the server record rather than appending a
    duplicate.

    Returns how many local records changed."""
    records = read_actions(path)
    by_code = {
        invite.get("acceptCode"): invite for invite in server_invites if invite.get("acceptCode")
    }
    by_client_request_id = {
        invite.get("clientRequestId"): invite
        for invite in server_invites
        if invite.get("clientRequestId")
    }
    changed = 0
    for record in records:
        server = by_code.get(record.get("accept_code")) or by_client_request_id.get(
            record.get("client_request_id")
        )
        if not server:
            continue
        updates = {}
        accept_code = server.get("acceptCode")
        if accept_code and accept_code != record.get("accept_code"):
            updates["accept_code"] = accept_code
        if accept_code:
            accept_url = server.get("acceptUrl") or f"{ACCEPT_URL_BASE}/{accept_code}"
            if accept_url != record.get("accept_url"):
                updates["accept_url"] = accept_url
            if record.get("status") != "invited":
                updates["status"] = "invited"
            if record.get("synced") is not True:
                updates["synced"] = True
            if record.get("delivery") is None:
                updates["delivery"] = record.get("requested_delivery", "email")
        if server.get("status") and server["status"] != record.get("server_status"):
            updates["server_status"] = server["status"]
        if server.get("emailDelivery") and server["emailDelivery"] != record.get("email_delivery"):
            updates["email_delivery"] = server["emailDelivery"]
        email_attempt = {
            local_key: server[server_key]
            for server_key, local_key in (
                ("emailAttemptState", "email_attempt_state"),
                ("emailAttemptStartedAt", "email_attempt_started_at"),
                (
                    "emailReconciliationRequired",
                    "email_reconciliation_required",
                ),
            )
            if server_key in server
        }
        for key, value in _email_attempt_fields(email_attempt).items():
            if key not in record or value != record.get(key):
                updates[key] = value
        if server.get("id") and server["id"] != record.get("server_invite_id"):
            updates["server_invite_id"] = server["id"]
        if server.get("acceptedAt") and server["acceptedAt"] != record.get("accepted_at"):
            updates["accepted_at"] = server["acceptedAt"]
        if updates:
            record.update(updates)
            if accept_code:
                record.pop("draft_reason", None)
                record.pop("requested_delivery", None)
            changed += 1

    if changed:
        write_private_json(path, records)
    return changed


def read_actions(path: str | None) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        # Preserve the damaged file for manual recovery instead of overwriting
        # the only evidence when the next action is saved.
        corrupt_path = f"{path}.corrupt-{uuid.uuid4().hex}"
        try:
            os.replace(path, corrupt_path)
            os.chmod(corrupt_path, 0o600)
        except OSError:
            pass
        return []
    except OSError:
        return []
