import os

from pluribus.api import invite_payload, packet_payload, replace_payload, scan_payload
from pluribus.engine import ClearanceEngine
from pluribus.invites import record_action
from pluribus.roster import Roster

SEED = os.path.join(os.path.dirname(__file__), "..", "seed", "roster.json")


def _engine():
    return ClearanceEngine(Roster.from_json(SEED))


def test_scan_payload_serializes_blake_fields_and_state_actions():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    out = scan_payload(api, _engine())
    person = out["persons"][0]
    assert out["summary"]["needs_review"] == 1
    assert person["state"] == "needs_review"
    assert person["name"] == "Marcus Reed"
    assert person["scope"] == ""
    assert person["union_status"] == "non-union"
    assert person["synthetic_only"] is False
    assert person["replacement_asset_key"] == "sarah_ref.png"
    assert person["available_actions"] == ["invite", "route", "replace"]


def test_scan_payload_unidentified_never_invites():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "stock_crowd_julia.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["state"] == "unidentified"
    assert person["available_actions"] == ["identify", "route", "replace"]


def test_scan_payload_synthetic_has_no_invite_or_replacement():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a prompt-only barista portrait", "clip": ["4", 1]},
        },
        "7": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["state"] == "synthetic_unverified"
    assert person["available_actions"] == []
    assert person["replacement_asset_key"] == ""


def test_replace_payload_wraps_updated_workflow():
    api = {"1": {"class_type": "LoadImage", "inputs": {"image": "elena_ref.png"}}}
    out = replace_payload({"workflow": api, "source_key": "elena_ref.png", "new_asset_key": "sarah_ref.png"})
    assert out["workflow"]["1"]["inputs"]["image"] == "sarah_ref.png"


def test_invite_payload_records_and_messages(tmp_path):
    path = str(tmp_path / "invites.json")
    out = invite_payload(
        {"kind": "invite", "name": "Marcus Reed", "source_key": "marcus_ref.png"},
        path,
    )
    assert out["action"]["status"] == "invited"
    assert "Marcus Reed" in out["message"]


def test_packet_payload_returns_json_markdown_and_recorded_actions(tmp_path):
    path = str(tmp_path / "invites.json")
    record_action(path, "invite", talent_id="t_marcus", name="Marcus Reed", source_key="marcus_ref.png")
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    out = packet_payload(api, _engine(), path)
    assert out["packet"]["summary"]["needs_review"] == 1
    assert out["packet"]["actions"][0]["name"] == "Marcus Reed"
    assert "Pluribus Approval Packet" in out["markdown"]
    assert "Actions taken" in out["markdown"]


def test_scan_payload_includes_source_node_id():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["source_node_id"] == "1"


def test_action_payload_invite_carries_email_and_accept_url(tmp_path):
    from pluribus.api import action_payload

    out = action_payload(
        {
            "kind": "invite",
            "talent_id": "t_marcus",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
            "email": "marcus@example.com",
            "note": "hi",
            "delivery": "email",
        },
        str(tmp_path / "invites.json"),
    )
    assert out["action"]["email"] == "marcus@example.com"
    assert out["action"]["accept_url"].startswith(
        "https://trypluribus.com/accept/PL-"
    )
    assert out["message"] == "Invite recorded for Marcus Reed."


def test_roster_payload_lists_all_talent():
    from pluribus.api import roster_payload

    out = roster_payload(_engine())
    names = [t["name"] for t in out["talent"]]
    assert "Sarah Chen" in names
    assert "Elena Vasquez" in names
    sarah = next(t for t in out["talent"] if t["name"] == "Sarah Chen")
    assert sarah["clearance_status"] == "cleared"
    assert sarah["asset_keys"]


def test_scan_payload_serializes_ops():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["ops"] == [{"node_id": "2", "class_type": "ReActorFaceSwap"}]
