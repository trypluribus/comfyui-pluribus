from __future__ import annotations

from .adapter import WorkflowAdapter
from .engine import ClearanceEngine
from .invites import read_actions, record_action
from .models import ClearanceState, PersonInstance
from .packet import build_packet, render_markdown
from .replace import build_replacement


def _available_actions(person: PersonInstance) -> list[str]:
    if person.state == ClearanceState.NEEDS_REVIEW:
        return ["invite", "route", "replace"]
    if person.state == ClearanceState.RESTRICTED:
        return ["route", "replace"]
    if person.state == ClearanceState.UNIDENTIFIED:
        return ["identify", "route", "replace"]
    return []


def _person_to_dict(person: PersonInstance) -> dict:
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
        "available_actions": _available_actions(person),
    }


def scan_payload(workflow_json: dict, engine: ClearanceEngine) -> dict:
    result = engine.scan(WorkflowAdapter.from_comfyui_api(workflow_json))
    return {
        "summary": result.summary(),
        "persons": [_person_to_dict(person) for person in result.persons],
    }


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


def action_payload(body: dict, actions_path: str) -> dict:
    kind = body.get("kind", "invite")
    record = record_action(
        actions_path,
        kind,
        talent_id=body.get("talent_id"),
        name=body.get("name", "Unknown"),
        source_key=body.get("source_key", ""),
        email=body.get("email", ""),
        note=body.get("note", ""),
        delivery=body.get("delivery", ""),
    )
    verb = VERB_BY_KIND.get(kind, "Action recorded for")
    return {"action": record, "message": f"{verb} {record['name']}."}


def invite_payload(body: dict, actions_path: str) -> dict:
    return action_payload(body, actions_path)


def packet_payload(
    workflow_json: dict,
    engine: ClearanceEngine,
    actions_path: str | None = None,
) -> dict:
    result = engine.scan(WorkflowAdapter.from_comfyui_api(workflow_json))
    actions = read_actions(actions_path)
    packet = build_packet(result, actions)
    return {"packet": packet, "markdown": render_markdown(packet)}
