from __future__ import annotations

from .models import PersonInstance, ScanResult

DISCLAIMER = (
    "Approval / evidence bundle for business-affairs review. "
    "This is not legal clearance."
)


def _person_row(person: PersonInstance) -> dict:
    return {
        "output_node_id": person.output_node_id,
        "name": person.name or "Unidentified person",
        "state": person.state.value,
        "source_kind": person.source_kind,
        "source_key": person.source_key,
        "provenance": person.provenance,
        "allowed_uses": person.allowed_uses,
        "prohibited_uses": person.prohibited_uses,
        "conflicts": person.conflicts,
        "scope": person.scope,
        "union_status": person.union_status,
        "rep": person.rep,
        "synthetic_only": person.synthetic_only,
        "replacement_asset_key": person.replacement_asset_key,
        "note": person.note,
    }


def build_packet(result: ScanResult, actions: list[dict] | None = None) -> dict:
    return {
        "disclaimer": DISCLAIMER,
        "summary": result.summary(),
        "talent": [_person_row(person) for person in result.persons],
        "actions": actions or [],
    }


def render_markdown(packet: dict) -> str:
    lines = ["# Pluribus Approval Packet", "", f"> {packet['disclaimer']}", ""]

    lines.append("## Summary")
    for state, count in packet["summary"].items():
        lines.append(f"- **{state.replace('_', ' ')}**: {count}")
    lines.append("")

    lines.append("## Talent")
    for row in packet["talent"]:
        lines.append(f"### {row['name']} - `{row['state']}`")
        lines.append(f"- Output node: `{row['output_node_id']}`")
        lines.append(f"- Source: {row['source_kind']} `{row['source_key']}`")
        lines.append(f"- Provenance: {' -> '.join(row['provenance'])}")
        lines.append(f"- Scope: {row['scope'] or 'Not on file'}")
        lines.append(f"- Union status: {row['union_status'] or 'Not on file'}")
        lines.append(f"- Rep: {row['rep'] or 'Not on file'}")
        lines.append(f"- Synthetic only: {str(row['synthetic_only']).lower()}")
        if row["allowed_uses"]:
            lines.append(f"- Allowed: {', '.join(row['allowed_uses'])}")
        if row["prohibited_uses"]:
            lines.append(f"- Prohibited: {', '.join(row['prohibited_uses'])}")
        if row["conflicts"]:
            lines.append(f"- Conflicts: {', '.join(row['conflicts'])}")
        lines.append(f"- Replacement asset key: `{row['replacement_asset_key'] or 'none'}`")
        lines.append(f"- Note: {row['note']}")
        lines.append("")

    if packet.get("actions"):
        lines.append("## Actions taken")
        for action in packet["actions"]:
            lines.append(
                f"- {action['kind']} -> {action['status']}: "
                f"{action['name']} (`{action['source_key']}`)"
            )
        lines.append("")

    return "\n".join(lines)
