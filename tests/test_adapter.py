from pluribus.adapter import WorkflowAdapter

API = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
    "6": {
        "class_type": "LoraLoader",
        "inputs": {"lora_name": "Sarah_Chen_ID_SDXL.safetensors", "model": ["4", 0]},
    },
    "7": {"class_type": "KSampler", "inputs": {"model": ["6", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    "99": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}},
}


def test_from_comfyui_api_accepts_wrapped_and_raw():
    assert WorkflowAdapter.from_comfyui_api(API).nodes == API
    assert WorkflowAdapter.from_comfyui_api({"prompt": API}).nodes == API


def test_terminal_image_nodes_found():
    assert set(WorkflowAdapter.from_comfyui_api(API).terminal_image_nodes()) == {"9", "99"}


def test_upstream_node_ids_traces_chain():
    assert set(WorkflowAdapter.from_comfyui_api(API).upstream_node_ids("9")) == {"7", "6", "4"}


def test_nodes_of_type_includes_self_and_upstream():
    found = WorkflowAdapter.from_comfyui_api(API).nodes_of_type("9", {"LoraLoader"})
    assert [node_id for node_id, _node in found] == ["6"]


def test_provenance_path_is_ordered_class_types():
    path = WorkflowAdapter.from_comfyui_api(API).provenance_path("9")
    assert path == ["SaveImage", "KSampler", "LoraLoader", "CheckpointLoaderSimple"]


def test_downstream_node_ids_follow_outputs():
    from pluribus.adapter import WorkflowAdapter

    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"image": ["2", 0]}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": "unrelated"}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }
    adapter = WorkflowAdapter.from_comfyui_api(api)
    assert adapter.downstream_node_ids("1") == ["2", "3", "9"]
    assert adapter.downstream_node_ids("9") == []


def test_save_video_is_a_terminal_output_node():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "2": {"class_type": "KlingImage2VideoNode", "inputs": {"start_frame": ["1", 0]}},
        "3": {"class_type": "SaveVideo", "inputs": {"video": ["2", 0]}},
    }
    assert WorkflowAdapter.from_comfyui_api(api).terminal_image_nodes() == ["3"]


def test_downstream_node_ids_dedupes_diamond_joins():
    # LoRA feeds KSampler twice: via model and via CLIP -> conditioning.
    api = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "2": {"class_type": "LoraLoader", "inputs": {"lora_name": "x.safetensors", "model": ["1", 0], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "hi", "clip": ["2", 1]}},
        "4": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
    }
    found = WorkflowAdapter.from_comfyui_api(api).downstream_node_ids("2")
    assert found == ["3", "4", "9"]
    assert len(found) == len(set(found))


def test_scan_terminal_nodes_includes_declared_outputs_and_custom_sinks():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        "10": {"class_type": "LoadAudio", "inputs": {"audio": "voice.wav"}},
        "11": {"class_type": "PartnerVoiceNode", "inputs": {"reference_audio": ["10", 0]}},
    }

    adapter = WorkflowAdapter.from_comfyui_api(api)

    assert adapter.terminal_image_nodes() == ["2"]
    assert adapter.scan_terminal_nodes() == ["2", "11"]


def test_source_specific_path_excludes_siblings_and_preserves_destination_input():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
        "2": {"class_type": "FluxKontextProImageNode", "inputs": {"image": ["1", 0]}},
        "3": {"class_type": "LoadVideo", "inputs": {"file": "motion.mp4"}},
        "4": {"class_type": "RunwayAleph2VideoToVideoNode", "inputs": {"video": ["3", 0]}},
        "5": {
            "class_type": "ByteDance2ReferenceNode",
            "inputs": {
                "model.reference_images.image_1": ["2", 0],
                "model.reference_videos.video_1": ["4", 0],
            },
        },
        "9": {"class_type": "SaveVideo", "inputs": {"video": ["5", 0]}},
    }
    adapter = WorkflowAdapter.from_comfyui_api(api)

    assert adapter.source_path_node_ids("1", "9") == ["9", "5", "2", "1"]
    assert adapter.source_provenance_path("1", "9") == [
        "SaveVideo",
        "ByteDance2ReferenceNode",
        "FluxKontextProImageNode",
        "LoadImage",
    ]
    assert adapter.source_input_names("1", "5") == [
        "model.reference_images.image_1"
    ]
    assert adapter.source_path_node_ids("3", "9") == ["9", "5", "4", "3"]
