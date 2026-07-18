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
    assert person.output_node_ids == []
    assert person.source_node_ids == ["30"]
    assert person.occurrences == []


def test_matching_marker_enriches_source_without_adding_an_occurrence():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "custom_actor.png"}},
        "2": {
            "class_type": "FluxKontextProImageNode",
            "inputs": {"input_image": ["1", 0]},
        },
        "5": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        "30": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "custom_actor.png",
                "display_name": "Custom Actor",
                "note": "Lead performer reference supplied by production.",
            },
        },
    }

    result = _scan(api)

    assert len(result.persons) == 1
    person = result.persons[0]
    assert person.name == "Custom Actor"
    assert person.note == "Lead performer reference supplied by production."
    assert person.output_node_id == "5"
    assert person.source_node_id == "1"
    assert person.output_node_ids == ["5"]
    assert person.source_node_ids == ["1"]
    assert [(item["output_node_id"], item["source_node_id"]) for item in person.occurrences] == [
        ("5", "1")
    ]


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


def test_source_marker_preserves_audio_unknown_and_keyed_prompt_kinds():
    api = {
        "40": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "audio",
                "source_key": "voice-performance.m4a",
                "display_name": "Voice performance",
                "note": "",
            },
        },
        "41": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "unknown",
                "source_key": "legacy-asset-17",
                "display_name": "Legacy source",
                "note": "",
            },
        },
        "42": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "prompt",
                "source_key": "prompt-block-hero",
                "display_name": "Prompt-only hero",
                "note": "No external identity reference.",
            },
        },
    }

    by_key = {person.source_key: person for person in _scan(api).persons}

    assert by_key["voice-performance.m4a"].source_kind == "audio"
    assert by_key["legacy-asset-17"].source_kind == "unknown"
    assert by_key["prompt-block-hero"].source_kind == "prompt"
    assert by_key["prompt-block-hero"].state == ClearanceState.SYNTHETIC_UNVERIFIED


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


def test_style_lab_groups_one_source_across_three_outputs_and_keeps_second_source():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "lf_still_night_s02.png"}},
        "2": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "3": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "4": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        "6": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": "lf_still_fight_s02.png"}},
        "9": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["8", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["9", 0]}},
    }

    result = _scan(api)

    assert len(result.persons) == 2
    assert result.summary()["unidentified"] == 2
    nightmare, fight = result.persons

    assert nightmare.source_key == "lf_still_night_s02.png"
    assert nightmare.output_node_id == "5"
    assert nightmare.source_node_id == "1"
    assert nightmare.output_node_ids == ["5", "6", "7"]
    assert nightmare.source_node_ids == ["1"]
    assert [operation["node_id"] for operation in nightmare.ops] == ["1", "2", "3", "4"]
    assert [occurrence["output_node_id"] for occurrence in nightmare.occurrences] == [
        "5",
        "6",
        "7",
    ]
    assert [
        [operation["node_id"] for operation in occurrence["ops"]]
        for occurrence in nightmare.occurrences
    ] == [["1", "2"], ["1", "3"], ["1", "4"]]

    assert fight.source_key == "lf_still_fight_s02.png"
    assert fight.output_node_ids == ["13"]
    assert fight.source_node_ids == ["8"]
    assert [operation["node_id"] for operation in fight.ops] == ["8", "9"]


def test_source_identity_groups_only_exact_nonempty_keys():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "actor.png"}},
        "2": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": "actor.png"}},
        "9": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["8", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["9", 0]}},
        "14": {"class_type": "LoadImage", "inputs": {"image": "Actor.png"}},
        "15": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["14", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["15", 0]}},
        "20": {"class_type": "LoadImage", "inputs": {"image": " actor.png "}},
        "21": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["20", 0]}},
        "22": {"class_type": "SaveImage", "inputs": {"images": ["21", 0]}},
    }

    persons = _scan(api).persons

    assert len(persons) == 3
    assert persons[0].output_node_ids == ["5", "13"]
    assert persons[0].source_node_ids == ["1", "8"]
    assert [person.source_key for person in persons] == [
        "actor.png",
        "Actor.png",
        " actor.png ",
    ]


def test_keyless_sources_fall_back_to_source_node_identity():
    api = {
        "1": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": []}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        "3": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": []}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }

    persons = _scan(api).persons

    assert len(persons) == 2
    assert [person.source_node_id for person in persons] == ["1", "3"]
    assert [person.ops[0]["class_type"] for person in persons] == [
        "ReActorFaceSwap",
        "ReActorFaceSwap",
    ]


def test_source_node_id_points_at_reference_image_behind_face_swap():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    assert _scan(api).persons[0].source_node_id == "1"


def test_face_swap_collects_source_and_base_images_without_modality_suppression():
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
    assert len(persons) == 2
    assert {person.source_key for person in persons} == {
        "background_plate.png",
        "marcus_ref.png",
    }
    by_key = {person.source_key: person for person in persons}
    assert by_key["marcus_ref.png"].source_node_id == "2"
    assert by_key["marcus_ref.png"].state == ClearanceState.NEEDS_REVIEW
    background_swap = next(
        operation
        for operation in by_key["background_plate.png"].ops
        if operation["class_type"] == "ReActorFaceSwap"
    )
    marcus_swap = next(
        operation
        for operation in by_key["marcus_ref.png"].ops
        if operation["class_type"] == "ReActorFaceSwap"
    )
    assert background_swap["input_name"] == "input_image"
    assert marcus_swap["input_name"] == "source_image"


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
    assert [op["class_type"] for op in person.ops] == [
        "LoadImage",
        "ReActorFaceSwap",
        "KSampler",
    ]


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
    assert [op["class_type"] for op in person.ops] == [
        "LoadImage",
        "GeminiImage2Node",
    ]


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


def test_runway_video_lanes_detect_two_unique_reference_sources():
    api = {
        "10": {"class_type": "LoadVideo", "inputs": {"file": "lf_night_s02.mp4"}},
        "11": {
            "class_type": "RunwayAleph2VideoToVideoNode",
            "inputs": {"video": ["10", 0], "prompt": "restyle the girl's scene"},
        },
        "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0]}},
        "20": {"class_type": "LoadVideo", "inputs": {"file": "lf_fight_s02.mp4"}},
        "21": {
            "class_type": "RunwayAleph2VideoToVideoNode",
            "inputs": {"video": ["20", 0], "prompt": "restyle the man's scene"},
        },
        "22": {"class_type": "SaveVideo", "inputs": {"video": ["21", 0]}},
    }

    persons = _scan(api).persons

    assert len(persons) == 2
    assert [person.source_kind for person in persons] == ["reference", "reference"]
    assert [person.source_key for person in persons] == [
        "lf_night_s02.mp4",
        "lf_fight_s02.mp4",
    ]
    assert [person.source_node_id for person in persons] == ["10", "20"]
    assert [person.output_node_id for person in persons] == ["12", "22"]
    assert [operation["node_id"] for operation in persons[0].ops] == ["10", "11"]
    assert [operation["node_id"] for operation in persons[1].ops] == ["20", "21"]
    assert persons[0].occurrences[0]["ops"] == [
        {
            "node_id": "10",
            "class_type": "LoadVideo",
            "source_role": "reference_video",
        },
        {
            "node_id": "11",
            "class_type": "RunwayAleph2VideoToVideoNode",
            "input_name": "video",
            "source_role": "reference_video",
        }
    ]


def test_direct_load_video_to_save_video_is_detected_as_a_source():
    api = {
        "10": {"class_type": "LoadVideo", "inputs": {"file": "source_take.mp4"}},
        "12": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0]}},
    }

    persons = _scan(api).persons

    assert len(persons) == 1
    person = persons[0]
    assert person.source_kind == "reference"
    assert person.source_key == "source_take.mp4"
    assert person.source_node_id == "10"
    assert person.output_node_id == "12"
    assert person.output_node_ids == ["12"]
    assert person.occurrences == [
        {
            "output_node_id": "12",
            "source_node_id": "10",
            "provenance": ["SaveVideo", "LoadVideo"],
            "ops": [
                {
                    "node_id": "10",
                    "class_type": "LoadVideo",
                    "source_role": "reference_video",
                }
            ],
        }
    ]


def test_mixed_media_sink_collects_every_external_source_without_opening_files():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "/missing/person.png"}},
        "2": {"class_type": "LoadVideo", "inputs": {"file": "/missing/motion.mp4"}},
        "3": {"class_type": "LoadAudio", "inputs": {"audio": "/missing/voice.wav"}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "5": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "performer.safetensors", "model": ["4", 0]},
        },
        "6": {
            "class_type": "ByteDance2ReferenceNode",
            "inputs": {
                "model": ["5", 0],
                "model.reference_images.image_1": ["1", 0],
                "model.reference_videos.video_1": ["2", 0],
                "model.reference_audios.audio_1": ["3", 0],
            },
        },
        "9": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0]}},
    }

    persons = _scan(api).persons

    assert {(person.source_kind, person.source_key) for person in persons} == {
        ("lora", "performer.safetensors"),
        ("reference", "/missing/person.png"),
        ("reference", "/missing/motion.mp4"),
        ("audio", "/missing/voice.wav"),
    }
    by_key = {person.source_key: person for person in persons}
    assert by_key["/missing/person.png"].replacement_asset_key == "sarah_ref.png"
    assert by_key["/missing/voice.wav"].replacement_asset_key == ""
    expected_roles = {
        "/missing/person.png": "reference_image",
        "/missing/motion.mp4": "reference_video",
        "/missing/voice.wav": "reference_audio",
    }
    for key, expected_role in expected_roles.items():
        final_op = next(
            operation
            for operation in by_key[key].ops
            if operation["class_type"] == "ByteDance2ReferenceNode"
        )
        assert final_op["source_role"] == expected_role


def test_source_specific_ops_do_not_inherit_sibling_image_video_or_audio_lanes():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
        "2": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "3": {"class_type": "LoadVideo", "inputs": {"file": "motion.mp4"}},
        "4": {"class_type": "RunwayAleph2VideoToVideoNode", "inputs": {"video": ["3", 0]}},
        "5": {"class_type": "LoadAudio", "inputs": {"audio": "voice.m4a"}},
        "6": {
            "class_type": "ByteDance2ReferenceNode",
            "inputs": {
                "model.reference_images.image_1": ["2", 0],
                "model.reference_videos.video_1": ["4", 0],
                "model.reference_audios.audio_1": ["5", 0],
            },
        },
        "9": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0]}},
    }

    by_key = {person.source_key: person for person in _scan(api).persons}

    assert by_key["person.png"].provenance == [
        "SaveVideo",
        "ByteDance2ReferenceNode",
        "FluxKontextProImageNode",
        "LoadImage",
    ]
    assert [operation["class_type"] for operation in by_key["person.png"].ops] == [
        "LoadImage",
        "FluxKontextProImageNode",
        "ByteDance2ReferenceNode",
    ]
    assert by_key["motion.mp4"].provenance == [
        "SaveVideo",
        "ByteDance2ReferenceNode",
        "RunwayAleph2VideoToVideoNode",
        "LoadVideo",
    ]
    assert [operation["class_type"] for operation in by_key["motion.mp4"].ops] == [
        "LoadVideo",
        "RunwayAleph2VideoToVideoNode",
        "ByteDance2ReferenceNode",
    ]
    assert by_key["voice.m4a"].provenance == [
        "SaveVideo",
        "ByteDance2ReferenceNode",
        "LoadAudio",
    ]
    assert [operation["class_type"] for operation in by_key["voice.m4a"].ops] == [
        "LoadAudio",
        "ByteDance2ReferenceNode"
    ]


def test_destination_image_port_overrides_original_video_extension():
    api = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": "performance.mp4"}},
        "2": {"class_type": "VideoFrameExtract", "inputs": {"video": ["1", 0]}},
        "3": {
            "class_type": "ByteDance2ReferenceNode",
            "inputs": {"model.reference_images.image_1": ["2", 0]},
        },
        "4": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0]}},
    }

    person = _scan(api).persons[0]
    final_operation = next(
        operation
        for operation in person.ops
        if operation["class_type"] == "ByteDance2ReferenceNode"
    )

    assert final_operation == {
        "node_id": "3",
        "class_type": "ByteDance2ReferenceNode",
        "input_name": "model.reference_images.image_1",
        "source_role": "reference_image",
    }


def test_one_source_keeps_multiple_roles_on_the_same_operation():
    api = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": "performance.mp4"}},
        "2": {"class_type": "VideoFrameExtract", "inputs": {"video": ["1", 0]}},
        "3": {
            "class_type": "ByteDance2ReferenceNode",
            "inputs": {
                "model.reference_images.image_1": ["2", 0],
                "model.reference_videos.video_1": ["1", 0],
            },
        },
        "4": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0]}},
    }

    person = _scan(api).persons[0]
    final_operations = [
        operation
        for operation in person.ops
        if operation["class_type"] == "ByteDance2ReferenceNode"
    ]

    assert final_operations == [
        {
            "node_id": "3",
            "class_type": "ByteDance2ReferenceNode",
            "input_name": "model.reference_images.image_1",
            "source_role": "reference_image",
        },
        {
            "node_id": "3",
            "class_type": "ByteDance2ReferenceNode",
            "input_name": "model.reference_videos.video_1",
            "source_role": "reference_video",
        },
    ]
    assert person.occurrences[0]["ops"][-2:] == final_operations


def test_supported_source_in_custom_sink_is_scanned_with_coverage_warning():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "saved.png"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        "10": {"class_type": "LoadAudio", "inputs": {"audio": "voice.wav"}},
        "11": {"class_type": "PartnerVoiceNode", "inputs": {"voice": ["10", 0]}},
    }

    result = _scan(api)

    assert {person.source_key for person in result.persons} == {"saved.png", "voice.wav"}
    assert result.issues == [
        {
            "code": "unsupported_terminal_node",
            "node_id": "11",
            "class_type": "PartnerVoiceNode",
            "message": (
                "Scanned source lineage ending at unsupported terminal node "
                "'PartnerVoiceNode'. Downstream-use coverage may be incomplete."
            ),
        }
    ]
