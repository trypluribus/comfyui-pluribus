from __future__ import annotations

import copy

SWAPPABLE_KEYS = ("lora_name", "image")


def build_replacement(workflow_json: dict, source_key: str, new_asset_key: str) -> dict:
    updated = copy.deepcopy(workflow_json)
    nodes = updated.get("prompt", updated)
    for node in nodes.values():
        inputs = node.get("inputs", {})
        for field in SWAPPABLE_KEYS:
            if inputs.get(field) == source_key:
                inputs[field] = new_asset_key
    return updated
