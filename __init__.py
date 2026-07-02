"""Pluribus AI Production Clearance Layer - ComfyUI adapter entry point."""

from __future__ import annotations

import json
import os
import sys

WEB_DIRECTORY = "./web"


def _cleared_roster_names() -> list[str]:
    try:
        path = os.path.join(os.path.dirname(__file__), "seed", "roster.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        names = [
            row["name"]
            for row in data.get("talent", [])
            if row.get("clearance_status") == "cleared"
        ]
        return names or ["Sarah Chen"]
    except Exception:  # pragma: no cover - defensive; dropdown must never break load.
        return ["Sarah Chen"]


class PluribusSourceMarker:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_kind": (["reference", "lora", "prompt", "unknown"], {"default": "reference"}),
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


class PluribusClearedTalent:
    """Cleared talent from the Pluribus roster, dropped into the graph.

    Emits a placeholder likeness reference (striped frame, deterministic per
    talent) plus the talent name so downstream nodes and the rights scan can
    track the digital twin. Real reference assets stay in Pluribus; only a
    scoped stand-in flows downstream.
    """

    @classmethod
    def INPUT_TYPES(cls):
        names = _cleared_roster_names()
        return {"required": {"talent": (names, {"default": names[0]})}}

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("reference", "talent_name")
    FUNCTION = "emit"
    CATEGORY = "Pluribus"
    DESCRIPTION = (
        "Cleared talent from your Pluribus roster. Consent scope travels with the "
        "likeness; downstream operations on the digital twin are tracked."
    )

    def emit(self, talent):
        import torch  # ComfyUI runtime dependency; not needed for tests.

        height, width = 640, 512
        hue = sum(ord(ch) for ch in talent) * 31 % 360
        top = torch.tensor(_hsv_to_rgb(hue / 360.0, 0.28, 0.38))
        bottom = torch.tensor(_hsv_to_rgb(((hue + 40) % 360) / 360.0, 0.24, 0.22))
        ramp = torch.linspace(0.0, 1.0, height).view(height, 1, 1)
        image = top * (1.0 - ramp) + bottom * ramp
        image = image.expand(height, width, 3).unsqueeze(0).contiguous()
        return (image, talent)


def _hsv_to_rgb(h: float, s: float, v: float) -> list[float]:
    i = int(h * 6.0) % 6
    f = h * 6.0 - int(h * 6.0)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    return [
        [v, t, p],
        [q, v, p],
        [p, v, t],
        [p, q, v],
        [t, p, v],
        [v, p, q],
    ][i]


NODE_CLASS_MAPPINGS: dict = {
    "PluribusSourceMarker": PluribusSourceMarker,
    "PluribusClearedTalent": PluribusClearedTalent,
}
NODE_DISPLAY_NAME_MAPPINGS: dict = {
    "PluribusSourceMarker": "Pluribus Source Marker",
    "PluribusClearedTalent": "Pluribus · Cleared Talent",
}

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from server import PromptServer  # type: ignore[import-not-found]

    from pluribus.server import register_routes

    register_routes(
        PromptServer.instance,
        roster_path=os.path.join(_HERE, "seed", "roster.json"),
        actions_path=os.path.join(_HERE, "data", "invites.json"),
    )
    print("[Pluribus] Clearance layer routes registered.")
except Exception as exc:  # pragma: no cover - ComfyUI runtime only.
    print(f"[Pluribus] Route registration skipped: {exc}")

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
