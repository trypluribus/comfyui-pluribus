from pluribus.replace import build_replacement


def test_build_replacement_swaps_lora_without_mutating_original():
    api = {
        "6": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "marcus_ref.png", "model": ["4", 0]},
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
    }
    updated = build_replacement(
        api,
        source_key="marcus_ref.png",
        new_asset_key="Sarah_Chen_ID_SDXL.safetensors",
    )
    assert updated["6"]["inputs"]["lora_name"] == "Sarah_Chen_ID_SDXL.safetensors"
    assert updated["4"]["inputs"]["ckpt_name"] == "sd.safetensors"
    assert api["6"]["inputs"]["lora_name"] == "marcus_ref.png"


def test_build_replacement_swaps_load_image():
    api = {"1": {"class_type": "LoadImage", "inputs": {"image": "elena_ref.png"}}}
    updated = build_replacement(api, source_key="elena_ref.png", new_asset_key="sarah_ref.png")
    assert updated["1"]["inputs"]["image"] == "sarah_ref.png"


def test_build_replacement_handles_wrapped_prompt():
    api = {"prompt": {"1": {"class_type": "LoadImage", "inputs": {"image": "elena_ref.png"}}}}
    updated = build_replacement(api, source_key="elena_ref.png", new_asset_key="sarah_ref.png")
    assert updated["prompt"]["1"]["inputs"]["image"] == "sarah_ref.png"
    assert api["prompt"]["1"]["inputs"]["image"] == "elena_ref.png"
