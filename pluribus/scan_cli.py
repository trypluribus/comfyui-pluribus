from __future__ import annotations

import json
import os
import sys

from .adapter import WorkflowAdapter
from .engine import ClearanceEngine
from .invites import read_actions
from .packet import build_packet, render_markdown
from .roster import Roster

BADGE = {
    "cleared": "ROSTER / SCOPE ON FILE",
    "needs_review": "NEEDS REVIEW",
    "restricted": "RESTRICTED",
    "synthetic_unverified": "SYNTHETIC (UNVERIFIED)",
    "unidentified": "UNIDENTIFIED",
}


def run(workflow_path: str, roster_path: str, out_dir: str) -> dict[str, int]:
    workflow_path = os.path.abspath(workflow_path)
    roster_path = os.path.abspath(roster_path)
    out_dir = os.path.abspath(out_dir)

    with open(workflow_path, encoding="utf-8") as handle:
        adapter = WorkflowAdapter.from_comfyui_api(json.load(handle))

    engine = ClearanceEngine(Roster.from_json(roster_path))
    result = engine.scan(adapter)

    print("\nPluribus Rights Scan")
    print("=" * 48)
    for person in result.persons:
        who = person.name or "Unidentified person"
        print(f"{BADGE.get(person.state.value, person.state.value):<26} {who}")
        print(f"     provenance: {' -> '.join(person.provenance)}")
        print(f"     {person.note}")
        if person.replacement_asset_key:
            print(f"     replacement: {person.replacement_asset_key}")
    print("=" * 48)
    print("Summary:", result.summary())

    os.makedirs(out_dir, exist_ok=True)
    actions = read_actions(os.path.join(out_dir, "invites.json"))
    packet = build_packet(result, actions)
    packet_path = os.path.join(out_dir, "approval_packet.md")
    with open(packet_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(packet))
    print(f"Approval packet written to {packet_path}\n")
    return result.summary()


def main() -> None:
    here = os.path.dirname(__file__)
    workflow_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.abspath(os.path.join(here, "..", "fixtures", "morning_people_spot_workflow_api.json"))
    )
    roster_path = (
        sys.argv[2]
        if len(sys.argv) > 2
        else os.path.abspath(os.path.join(here, "..", "seed", "roster.json"))
    )
    out_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.abspath(os.path.join(here, "..", "data"))
    run(workflow_path, roster_path, out_dir)


if __name__ == "__main__":
    main()
