import json
import os

from pluribus.adapter import WorkflowAdapter
from pluribus.engine import ClearanceEngine
from pluribus.roster import Roster

WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pluribus_marker_workflow.json")
SEED = os.path.join(os.path.dirname(__file__), "..", "seed", "roster.json")


def test_marker_workflow_fixture_is_loadable_and_scannable():
    with open(WORKFLOW, encoding="utf-8") as handle:
        workflow = json.load(handle)

    assert workflow["nodes"]
    assert all(node["type"] == "PluribusSourceMarker" for node in workflow["nodes"])

    api_prompt = {}
    for node in workflow["nodes"]:
        source_kind, source_key, display_name, note = node["widgets_values"]
        api_prompt[str(node["id"])] = {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": source_kind,
                "source_key": source_key,
                "display_name": display_name,
                "note": note,
            },
        }

    result = ClearanceEngine(Roster.from_json(SEED)).scan(WorkflowAdapter.from_comfyui_api(api_prompt))
    summary = result.summary()

    assert summary["cleared"] >= 3
    assert summary["needs_review"] == 1
    assert summary["restricted"] == 1
    assert summary["synthetic_unverified"] == 1
    assert summary["unidentified"] == 2


SPOT_UI = os.path.join(os.path.dirname(__file__), "..", "fixtures", "morning_people_spot_workflow.json")
SPOT_API = os.path.join(os.path.dirname(__file__), "..", "fixtures", "morning_people_spot_workflow_api.json")

# Every non-note type in the UI fixture; all verified against ComfyUI 0.25.0
# core via /object_info (no custom node packs).
SPOT_NODE_TYPES = {
    "CheckpointLoaderSimple",
    "LoraLoader",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "KSampler",
    "VAEDecode",
    "SaveImage",
    "KlingImage2VideoNode",
    "SaveVideo",
    "LoadImage",
    "GeminiImage2Node",
    "FluxKontextProImageNode",
    "MarkdownNote",
}


def test_morning_people_spot_ui_fixture_shape():
    with open(SPOT_UI, encoding="utf-8") as handle:
        workflow = json.load(handle)

    assert workflow["version"] == 0.4
    assert {node["type"] for node in workflow["nodes"]} == SPOT_NODE_TYPES
    assert len(workflow["groups"]) == 5
    node_ids = {node["id"] for node in workflow["nodes"]}
    for link_id, src, _src_slot, dst, _dst_slot, _ltype in workflow["links"]:
        assert src in node_ids and dst in node_ids, f"dangling link {link_id}"


def test_morning_people_spot_api_fixture_trips_all_five_states():
    with open(SPOT_API, encoding="utf-8") as handle:
        api = json.load(handle)

    result = ClearanceEngine(Roster.from_json(SEED)).scan(WorkflowAdapter.from_comfyui_api(api))
    assert result.summary() == {
        "cleared": 2,  # Sarah: hero still + hero video output
        "needs_review": 1,  # Marcus reference via Nano Banana composite
        "restricted": 1,  # Elena reference via Flux Kontext edit
        "unidentified": 1,  # community LoRA of unknown origin
        "synthetic_unverified": 1,  # prompt-only barista B-roll
    }

    by_state = {p.state.value: p for p in result.persons}
    assert by_state["cleared"].name == "Sarah Chen"
    assert "KlingImage2VideoNode" in [op["class_type"] for op in by_state["cleared"].ops]
    assert by_state["needs_review"].source_key == "marcus_ref.png"
    assert by_state["restricted"].source_key == "elena_ref.png"
    assert by_state["unidentified"].source_key == "coffeehouse_regulars_SDXL_v3.safetensors"
