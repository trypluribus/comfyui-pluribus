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
RIGHTS_STRESS_API = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "rights_stress_test_workflow_api.json"
)
LITTLE_FLOWER_API = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "little_flower_reverse_workflow_api.json"
)

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
        "cleared": 1,  # Sarah: one source reused for hero still and video outputs
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


def test_rights_stress_fixture_has_stable_complete_source_inventory():
    with open(RIGHTS_STRESS_API, encoding="utf-8") as handle:
        api = json.load(handle)

    result = ClearanceEngine(Roster([])).scan(WorkflowAdapter.from_comfyui_api(api))

    assert result.issues == []
    assert result.summary() == {
        "cleared": 0,
        "needs_review": 0,
        "restricted": 0,
        "synthetic_unverified": 0,
        "unidentified": 8,
    }
    by_key = {person.source_key: person for person in result.persons}
    assert set(by_key) == {
        "alhassan_lrx26_sdxl_webcam_v0.safetensors",
        "IMG_0003.jpeg",
        "IMG_2505.jpeg",
        "hero-cast-final_00002_.png",
        "TYLER_FACE_MASTER_A.jpg",
        "TYLER_BODY_GYM_A.jpg",
        "TYLER_TATTOO_CONDITIONING_V02_3840x2160.png",
        "little_flower/lf_fight_s02.mp4",
    }
    assert by_key["alhassan_lrx26_sdxl_webcam_v0.safetensors"].source_kind == "lora"
    assert by_key["alhassan_lrx26_sdxl_webcam_v0.safetensors"].output_node_ids == [
        "14",
        "19",
        "23",
        "24",
        "25",
        "27",
        "32",
    ]
    assert by_key["little_flower/lf_fight_s02.mp4"].source_kind == "reference"
    assert len(by_key["little_flower/lf_fight_s02.mp4"].occurrences) == 2


def test_little_flower_fixture_keeps_identity_evidence_and_audio_sources_distinct():
    with open(LITTLE_FLOWER_API, encoding="utf-8") as handle:
        api = json.load(handle)

    result = ClearanceEngine(Roster([])).scan(WorkflowAdapter.from_comfyui_api(api))

    assert result.issues == []
    assert result.summary() == {
        "cleared": 0,
        "needs_review": 0,
        "restricted": 0,
        "synthetic_unverified": 0,
        "unidentified": 43,
    }
    references = [person for person in result.persons if person.source_kind == "reference"]
    audio = [person for person in result.persons if person.source_kind == "audio"]
    assert len(references) == 35
    assert len(audio) == 8
    assert all(person.source_key.endswith(".m4a") for person in audio)
    assert all(person.replacement_asset_key == "" for person in audio)
    assert all(len(person.occurrences) == 1 for person in audio)
    assert all(
        next(
            operation
            for operation in person.ops
            if operation["class_type"] == "ByteDance2ReferenceNode"
        )["source_role"]
        == "reference_audio"
        for person in audio
    )
    by_key = {person.source_key: person for person in references}
    assert (
        len(
            by_key[
                "little_flower_reverse__nisreen_salem_as_layla_identity_evidence.png"
            ].occurrences
        )
        == 32
    )
    assert sum(
        person.source_key.startswith("little_flower_reverse__party_visual_candidate_")
        for person in references
    ) == 12
