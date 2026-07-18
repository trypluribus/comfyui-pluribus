"""Pluribus rights scan and terms workflow - ComfyUI adapter entry point."""

from __future__ import annotations

import os
import sys

WEB_DIRECTORY = "./web"

class PluribusSourceMarker:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_kind": (["reference", "audio", "lora", "prompt", "unknown"], {"default": "reference"}),
                "source_key": ("STRING", {"default": "", "multiline": False}),
                "display_name": ("STRING", {"default": "", "multiline": False}),
                "note": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("source_key",)
    FUNCTION = "mark"
    CATEGORY = "Pluribus"
    DESCRIPTION = "Annotates a person/source in the graph so Pluribus scan can inspect it."

    def mark(self, source_kind, source_key, display_name, note):
        return (source_key,)


NODE_CLASS_MAPPINGS: dict = {
    "PluribusSourceMarker": PluribusSourceMarker,
}
NODE_DISPLAY_NAME_MAPPINGS: dict = {
    "PluribusSourceMarker": "Pluribus Source Marker",
}

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _resolve_data_dir() -> str:
    """Writable directory for private connection and workflow-binding state.

    Cloud-hosted ComfyUI often mounts custom_nodes read-only; fall back from
    PLURIBUS_DATA_DIR -> <plugin>/data -> a per-user temp dir so the plugin
    keeps working instead of crashing on first project link.
    """
    candidates = [
        os.environ.get("PLURIBUS_DATA_DIR"),
        os.path.join(_HERE, "data"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            from pluribus.storage import ensure_private_dir

            ensure_private_dir(candidate)
            if os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    from pluribus.storage import fallback_private_data_dir

    fallback = fallback_private_data_dir(_HERE)
    print(
        "[Pluribus] Plugin directory is not writable; private plugin state will be "
        f"stored in {fallback} (set PLURIBUS_DATA_DIR to override)."
    )
    return fallback


try:
    from server import PromptServer  # type: ignore[import-not-found]

    from pluribus.server import register_routes

    _data_dir = _resolve_data_dir()
    register_routes(
        PromptServer.instance,
        roster_path=None,
        actions_path=os.path.join(_data_dir, "invites.json"),
        connection_path=os.path.join(_data_dir, "connection.json"),
        bindings_path=os.path.join(_data_dir, "bindings.json"),
    )
    print("[Pluribus] Rights workflow routes registered.")
except Exception as exc:  # pragma: no cover - ComfyUI runtime only.
    print(f"[Pluribus] Route registration skipped: {exc}")

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
