from pluribus.models import ClearanceState, PersonInstance, ScanResult
from pluribus.packet import DISCLAIMER, build_packet, render_markdown


def _result():
    return ScanResult(
        persons=[
            PersonInstance(
                "9",
                "lora",
                "Sarah_Chen_ID_SDXL.safetensors",
                ClearanceState.CLEARED,
                name="Sarah Chen",
                note="Cleared for scope: Small appliances",
                provenance=["SaveImage", "LoraLoader"],
                allowed_uses=["social"],
                prohibited_uses=["political"],
                scope="Small appliances, US",
                union_status="SAG-AFTRA",
                rep="CAA",
                synthetic_only=False,
            ),
            PersonInstance(
                "17",
                "reference",
                "elena_ref.png",
                ClearanceState.RESTRICTED,
                name="Elena Vasquez",
                conflicts=["Exclusivity on file"],
                scope="Restriction on file",
                union_status="SAG-AFTRA",
                rep="WME",
                replacement_asset_key="sarah_ref.png",
            ),
        ]
    )


def test_packet_includes_summary_persons_disclaimer_and_blake_fields():
    packet = build_packet(_result())
    assert packet["disclaimer"] == DISCLAIMER
    assert packet["summary"]["cleared"] == 1
    first = packet["talent"][0]
    assert first["name"] == "Sarah Chen"
    assert first["provenance"] == ["SaveImage", "LoraLoader"]
    assert first["allowed_uses"] == ["social"]
    assert first["scope"] == "Small appliances, US"
    assert first["union_status"] == "SAG-AFTRA"
    assert first["rep"] == "CAA"
    assert first["synthetic_only"] is False
    assert packet["talent"][1]["replacement_asset_key"] == "sarah_ref.png"


def test_markdown_render_carries_disclaimer_names_and_checklist_fields():
    md = render_markdown(build_packet(_result()))
    assert DISCLAIMER in md
    assert "Sarah Chen" in md
    assert "Elena Vasquez" in md
    assert "not legal clearance" in md.lower()
    assert "Union status" in md
    assert "Replacement asset key" in md


def test_packet_includes_action_history():
    actions = [
        {
            "kind": "invite",
            "status": "invited",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
        }
    ]
    packet = build_packet(_result(), actions)
    assert packet["actions"] == actions
    md = render_markdown(packet)
    assert "Actions taken" in md
    assert "Marcus Reed" in md
