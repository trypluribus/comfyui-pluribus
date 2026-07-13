from __future__ import annotations

import hashlib
import json

from .adapter import WorkflowAdapter
from .engine import ClearanceEngine
from .invites import (
    client_request_id_for_invite,
    latest_accepted_invite,
    normalize_scope_statements,
    read_actions,
    record_action,
)
from .models import ClearanceState, PersonInstance
from .packet import build_packet, render_markdown
from .replace import build_replacement


def _available_actions(person: PersonInstance) -> list[str]:
    # A local graph scan detects possible person sources; it cannot determine
    # rights or invitation eligibility. Those decisions begin only after the
    # user links the source to a canonical project/person in Pluribus.
    if person.state in {
        ClearanceState.NEEDS_REVIEW,
        ClearanceState.RESTRICTED,
        ClearanceState.SYNTHETIC_UNVERIFIED,
        ClearanceState.UNIDENTIFIED,
    }:
        return ["link", "not_person", "review"]
    return []


_SCOPE_RULES = (
    (
        {"LoraLoader", "LoraLoaderModelOnly"},
        "Generation from a model trained on their likeness",
    ),
    ({"LoadImage"}, "Use of their reference image as a generation source"),
    ({"ReActorFaceSwap"}, "Use of their face / likeness in this workflow"),
    (
        {"IPAdapter", "IPAdapterAdvanced", "IPAdapterApply"},
        "Conditioning AI generation on their face",
    ),
    (
        {"GeminiImage2Node", "FluxKontextProImageNode"},
        "AI editing of their reference image",
    ),
    (
        {"KlingImage2VideoNode"},
        "Animation of their likeness from image to video",
    ),
    ({"CLIPTextEncode"}, "AI depiction directed by a text prompt"),
)


def _scope_statements(person: PersonInstance) -> list[str]:
    classes = set(person.provenance)
    classes.update(str(operation.get("class_type") or "") for operation in person.ops)
    statements = [
        statement for class_types, statement in _SCOPE_RULES if classes & class_types
    ]
    return statements or ["Use of their likeness in this workflow"]


def workflow_fingerprint(workflow_json: dict) -> str:
    """Python fallback fingerprint for legacy raw scan requests.

    Wrapped browser requests supply the JS-computed canonical fingerprint so
    integral floats use JSON.stringify semantics consistently end to end.
    """
    canonical = json.dumps(
        workflow_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _person_to_dict(
    person: PersonInstance,
    accepted_invite: dict | None = None,
    scope_statements: list[str] | None = None,
    workflow_name: str = "",
    workflow_fingerprint_value: str = "",
) -> dict:
    available_actions = _available_actions(person)
    if accepted_invite:
        available_actions = [action for action in available_actions if action != "invite"]
    return {
        "output_node_id": person.output_node_id,
        "source_node_id": person.source_node_id,
        "source_kind": person.source_kind,
        "source_key": person.source_key,
        "state": person.state.value,
        "talent_id": person.talent_id,
        "name": person.name,
        "note": person.note,
        "provenance": person.provenance,
        "allowed_uses": person.allowed_uses,
        "prohibited_uses": person.prohibited_uses,
        "conflicts": person.conflicts,
        "scope": person.scope,
        "union_status": person.union_status,
        "rep": person.rep,
        "synthetic_only": person.synthetic_only,
        "replacement_asset_key": person.replacement_asset_key,
        "ops": person.ops,
        "workflow_name": workflow_name,
        "workflow_fingerprint": workflow_fingerprint_value,
        "scope_statements": scope_statements or _scope_statements(person),
        "terms_status": "accepted" if accepted_invite else None,
        "terms_accepted_at": accepted_invite.get("accepted_at") if accepted_invite else None,
        "available_actions": available_actions,
    }


def scan_payload(
    workflow_json: dict,
    engine: ClearanceEngine,
    actions_path: str | None = None,
    workflow_name: str = "",
    provided_workflow_fingerprint: str = "",
) -> dict:
    fingerprint = provided_workflow_fingerprint or workflow_fingerprint(workflow_json)
    result = engine.scan(WorkflowAdapter.from_comfyui_api(workflow_json))
    actions = read_actions(actions_path)
    workflow_name = str(workflow_name or "")
    persons = []
    for person in result.persons:
        scope_statements = _scope_statements(person)
        accepted_invite = latest_accepted_invite(
            actions,
            workflow_name=workflow_name,
            workflow_fingerprint=fingerprint,
            source_key=person.source_key,
            source_kind=person.source_kind,
            scope_statements=scope_statements,
            talent_id=person.talent_id,
        )
        persons.append(
            _person_to_dict(
                person,
                accepted_invite,
                scope_statements,
                workflow_name,
                fingerprint,
            )
        )
    return {
        "workflow_name": workflow_name,
        "workflow_fingerprint": fingerprint,
        "summary": result.summary(),
        "persons": persons,
        "issues": result.issues,
    }


def scan_request_payload(
    body: dict, engine: ClearanceEngine, actions_path: str | None = None
) -> dict:
    """Accept the current wrapped scan request and the legacy raw graph."""
    if isinstance(body.get("workflow"), dict) and "workflow_name" in body:
        return scan_payload(
            body["workflow"],
            engine,
            actions_path,
            workflow_name=body.get("workflow_name", ""),
            provided_workflow_fingerprint=body.get("workflow_fingerprint", ""),
        )
    return scan_payload(body, engine, actions_path)


def roster_payload(engine: ClearanceEngine) -> dict:
    return {
        "talent": [
            {
                "talent_id": asset.talent_id,
                "name": asset.name,
                "clearance_status": asset.clearance_status,
                "scope": asset.scope,
                "allowed_uses": asset.allowed_uses,
                "prohibited_uses": asset.prohibited_uses,
                "conflicts": asset.conflicts,
                "union_status": asset.union_status,
                "rep": asset.rep,
                "synthetic_only": asset.synthetic_only,
                "asset_keys": asset.asset_keys,
            }
            for asset in engine.roster.all_assets()
        ]
    }


def replace_payload(body: dict) -> dict:
    updated = build_replacement(
        body["workflow"],
        source_key=body["source_key"],
        new_asset_key=body["new_asset_key"],
    )
    return {"workflow": updated}


VERB_BY_KIND = {
    "invite": "Invite recorded for",
    "route": "Routed for review:",
    "identify": "Identification requested for",
}


def action_payload(body: dict, actions_path: str, remote_result: dict | None = None) -> dict:
    kind = body.get("kind", "invite")

    if kind == "invite" and remote_result and remote_result.get("state") == "validation_error":
        return {
            "action": None,
            "state": "validation_error",
            "message": remote_result.get("message") or "Enter a valid email address.",
        }

    client_request_id = ""
    if kind == "invite":
        client_request_id = client_request_id_for_invite(actions_path, body)

    override = None
    if remote_result and remote_result.get("state") == "sent":
        if remote_result.get("accept_code"):
            override = remote_result
        else:
            remote_result = {
                **remote_result,
                "state": "unconfirmed",
                "message": "Pluribus returned no accept code.",
            }

    draft_reason = ""
    if kind == "invite" and override is None:
        draft_reason = (remote_result or {}).get("state", "disconnected")
        if draft_reason == "offline":
            draft_reason = "unconfirmed"

    record = record_action(
        actions_path,
        kind,
        talent_id=body.get("talent_id"),
        name=body.get("name", "Unknown"),
        source_key=body.get("source_key", ""),
        source_kind=body.get("source_kind", ""),
        workflow_name=body.get("workflow_name", ""),
        workflow_fingerprint=body.get("workflow_fingerprint", ""),
        scope_statements=normalize_scope_statements(body.get("scope_statements")),
        email=body.get("email", ""),
        note=body.get("note", ""),
        delivery=body.get("delivery", ""),
        override=override,
        draft_reason=draft_reason,
        client_request_id=client_request_id,
        delivery_result=remote_result,
    )
    return {"action": record, "message": _action_message(kind, record, remote_result)}


def _action_message(kind: str, record: dict, remote_result: dict | None) -> str:
    if kind != "invite":
        verb = VERB_BY_KIND.get(kind, "Action recorded for")
        return f"{verb} {record['name']}."

    if record.get("status") == "invited":
        attempt_state = record.get("email_attempt_state")
        if (
            record.get("email_reconciliation_required") is True
            or attempt_state == "manual_reconciliation"
        ):
            return (
                f"Invite created on Pluribus for {record['name']}. Do not resend "
                "automatically: email delivery requires operator/provider review. "
                "Keep using this existing accept link and sync after reconciliation."
            )
        if attempt_state in {"ambiguous", "in_flight"}:
            return (
                f"Invite created on Pluribus for {record['name']}. The email may already "
                "have been sent. Retry or sync this same invite using its existing accept "
                "link; do not create a new invite."
            )
        if record.get("email_delivery") == "sent":
            return f"Invite emailed to {record.get('email') or record['name']} via Pluribus."
        return f"Invite created on Pluribus for {record['name']} — share the accept link."

    reason = record.get("draft_reason")
    if reason == "unconfirmed":
        return (
            f"Pluribus could not confirm the invite for {record['name']}; it may already "
            "have been created or emailed. Retry or sync this same request — the request "
            "ID will be reused."
        )
    if reason == "unauthorized":
        return (
            f"Your Pluribus connection expired — draft saved locally for {record['name']}. "
            "Reconnect to send it; no accept link was created."
        )
    if reason == "error":
        detail = str((remote_result or {}).get("message", "unknown error")).rstrip(". ")
        return (
            f"Pluribus error — draft saved locally for {record['name']}: {detail}. "
            "Nothing was sent and no accept link was created."
        )
    return (
        f"Draft saved locally for {record['name']}. Connect to Pluribus to send it; "
        "no accept link was created."
    )


def invite_payload(body: dict, actions_path: str, remote_result: dict | None = None) -> dict:
    return action_payload(body, actions_path, remote_result)


def packet_payload(
    workflow_json: dict,
    engine: ClearanceEngine,
    actions_path: str | None = None,
) -> dict:
    result = engine.scan(WorkflowAdapter.from_comfyui_api(workflow_json))
    actions = read_actions(actions_path)
    packet = build_packet(result, actions)
    return {"packet": packet, "markdown": render_markdown(packet)}
