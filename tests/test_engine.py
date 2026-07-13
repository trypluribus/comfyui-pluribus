import os

from pluribus.adapter import WorkflowAdapter
from pluribus.engine import ClearanceEngine
from pluribus.models import ClearanceState
from pluribus.roster import Roster

SEED = os.path.join(os.path.dirname(__file__), "..", "seed", "roster.json")


def _engine():
    return ClearanceEngine(Roster.from_json(SEED))


def _scan(api):
    return _engine().scan(WorkflowAdapter.from_comfyui_api(api))


def test_lora_cleared_person_with_uses_provenance_and_blake_fields():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "6": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Sarah_Chen_ID_SDXL.safetensors", "model": ["4", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["6", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.CLEARED
    assert person.name == "Sarah Chen"
    assert "social" in person.allowed_uses
    assert person.scope
    assert person.union_status == "SAG-AFTRA"
    assert person.rep == "CAA"
    assert person.synthetic_only is False
    assert person.provenance[0] == "SaveImage"
    assert person.replacement_asset_key == ""


def test_reference_pending_is_needs_review():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.NEEDS_REVIEW
    assert person.name == "Marcus Reed"
    assert person.replacement_asset_key == "sarah_ref.png"


def test_restricted_person_flags_conflict_without_campaign_overclaim():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "elena_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.RESTRICTED
    assert person.conflicts
    assert "restriction on file" in person.note.lower()
    assert "review against campaign" in person.note.lower()
    assert "blocks this use" not in person.note.lower()
    assert person.replacement_asset_key.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))


def test_prompt_only_is_synthetic_unverified_no_legal_claim():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a young woman barista smiling", "clip": ["4", 1]},
        },
        "7": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.SYNTHETIC_UNVERIFIED
    assert "no known real-person source" in person.note.lower()
    assert "no nil" not in person.note.lower()
    assert person.replacement_asset_key == ""


def test_unknown_reference_is_unidentified_with_reference_replacement():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "stock_crowd_julia.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.UNIDENTIFIED
    assert person.replacement_asset_key == "sarah_ref.png"


def test_landscape_output_has_no_person():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a mountain at sunset", "clip": ["4", 1]},
        },
        "7": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    }
    assert _scan(api).persons == []


def test_source_marker_node_is_scanned_without_output_edge():
    api = {
        "30": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "marcus_ref.png",
                "display_name": "",
                "note": "",
            },
        }
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.NEEDS_REVIEW
    assert person.name == "Marcus Reed"
    assert person.provenance == ["PluribusSourceMarker"]


def test_blank_source_marker_is_ignored_and_reported_as_incomplete():
    api = {
        "30": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "",
                "display_name": "",
                "note": "",
            },
        }
    }

    result = _scan(api)

    assert result.persons == []
    assert result.issues == [
        {
            "code": "incomplete_source_marker",
            "node_id": "30",
            "message": (
                "Pluribus Source Marker is incomplete and was ignored. "
                "Add a source key (or describe a prompt-only source)."
            ),
        }
    ]


def test_blank_duplicate_does_not_hide_populated_marker_fields():
    api = {
        "30": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "",
                "display_name": "",
                "note": "",
            },
        },
        "31": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "nadia-brooks-character-sheet-v1",
                "display_name": "Nadia Brooks",
                "note": "Lead runner character-sheet reference.",
            },
        },
    }

    result = _scan(api)

    assert len(result.persons) == 1
    assert result.persons[0].source_key == "nadia-brooks-character-sheet-v1"
    assert result.persons[0].name == "Nadia Brooks"
    assert result.persons[0].note == "Lead runner character-sheet reference."
    assert [issue["node_id"] for issue in result.issues] == ["30"]


def test_prompt_marker_without_key_requires_a_description():
    blank = {
        "40": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "prompt",
                "source_key": "",
                "display_name": "",
                "note": "",
            },
        }
    }
    described = {
        "41": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "prompt",
                "source_key": "",
                "display_name": "Prompt-only background runner",
                "note": "No external reference image or likeness model.",
            },
        }
    }

    assert _scan(blank).persons == []
    person = _scan(described).persons[0]
    assert person.name == "Prompt-only background runner"
    assert person.state == ClearanceState.SYNTHETIC_UNVERIFIED


def test_source_node_id_points_at_lora_loader():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "6": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Sarah_Chen_ID_SDXL.safetensors", "model": ["4", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["6", 0]}},
    }
    assert _scan(api).persons[0].source_node_id == "6"


def test_output_collects_multiple_person_sources():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "6": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Sarah_Chen_ID_SDXL.safetensors", "model": ["4", 0]},
        },
        "7": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "unknown-community-lora.safetensors", "model": ["6", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    }
    persons = _scan(api).persons
    states = {person.source_key: person.state for person in persons}
    assert states == {
        "Sarah_Chen_ID_SDXL.safetensors": ClearanceState.CLEARED,
        "unknown-community-lora.safetensors": ClearanceState.UNIDENTIFIED,
    }


def test_output_collects_lora_and_face_reference_sources():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "6": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Sarah_Chen_ID_SDXL.safetensors", "model": ["4", 0]},
        },
        "7": {
            "class_type": "ReActorFaceSwap",
            "inputs": {"input_image": ["6", 0], "source_image": ["1", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    }
    states = {person.source_key: person.state for person in _scan(api).persons}
    assert states == {
        "Sarah_Chen_ID_SDXL.safetensors": ClearanceState.CLEARED,
        "marcus_ref.png": ClearanceState.NEEDS_REVIEW,
    }


def test_source_node_id_points_at_reference_image_behind_face_swap():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    assert _scan(api).persons[0].source_node_id == "1"


def test_face_swap_uses_source_image_not_base_input_image():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "background_plate.png"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "3": {
            "class_type": "ReActorFaceSwap",
            "inputs": {"input_image": ["1", 0], "source_image": ["2", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }
    persons = _scan(api).persons
    assert len(persons) == 1
    assert persons[0].source_key == "marcus_ref.png"
    assert persons[0].source_node_id == "2"
    assert persons[0].state == ClearanceState.NEEDS_REVIEW


def test_source_node_id_points_at_person_prompt_encode():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a friendly barista", "clip": ["4", 1]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.SYNTHETIC_UNVERIFIED
    assert person.source_node_id == "5"


def test_source_node_id_points_at_marker_node():
    api = {
        "12": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "marcus_ref.png",
                "display_name": "",
                "note": "",
            },
        },
    }
    person = _scan(api).persons[0]
    assert person.source_node_id == "12"


def test_retired_cleared_talent_node_never_creates_a_roster_decision():
    api = {
        "12": {"class_type": "PluribusClearedTalent", "inputs": {"talent": "Sarah Chen"}},
    }
    assert _scan(api).persons == []


def test_reference_person_gets_downstream_ops():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "3": {"class_type": "KSampler", "inputs": {"latent_image": ["2", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }
    person = _scan(api).persons[0]
    assert [op["class_type"] for op in person.ops] == ["ReActorFaceSwap", "KSampler"]


def test_gemini_image_edit_reference_is_needs_review():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {
            "class_type": "GeminiImage2Node",
            "inputs": {"prompt": "place the man in a kitchen", "images": ["1", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.NEEDS_REVIEW
    assert person.source_kind == "reference"
    assert person.source_key == "marcus_ref.png"
    assert person.source_node_id == "1"
    assert [op["class_type"] for op in person.ops] == ["GeminiImage2Node"]


def test_flux_kontext_reference_is_restricted_for_elena():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "elena_ref.png"}},
        "2": {
            "class_type": "FluxKontextProImageNode",
            "inputs": {"prompt": "restyle the woman", "input_image": ["1", 0]},
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.RESTRICTED
    assert person.name == "Elena Vasquez"
    assert person.source_key == "elena_ref.png"


def test_save_video_output_classifies_lora_and_ops_skip_save_video():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "6": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Sarah_Chen_ID_SDXL.safetensors", "model": ["4", 0]},
        },
        "7": {"class_type": "KSampler", "inputs": {"model": ["6", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0]}},
        "9": {"class_type": "KlingImage2VideoNode", "inputs": {"start_frame": ["8", 0]}},
        "10": {"class_type": "SaveVideo", "inputs": {"video": ["9", 0]}},
    }
    person = _scan(api).persons[0]
    assert person.state == ClearanceState.CLEARED
    assert person.name == "Sarah Chen"
    ops = [op["class_type"] for op in person.ops]
    assert "KlingImage2VideoNode" in ops
    assert "SaveVideo" not in ops
    assert "CreateVideo" not in ops
