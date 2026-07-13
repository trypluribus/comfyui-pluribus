"""Pluribus rights scan and terms workflow - ComfyUI adapter entry point."""

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
    """Roster-linked talent placeholder dropped into the graph.

    Emits a placeholder likeness reference (striped frame, deterministic per
    talent) plus the talent name. The rights scan uses that name to look up the
    current local roster record and list supported downstream graph nodes. Real
    reference assets stay in Pluribus.
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
        "Roster-linked talent placeholder. Review scope from the current local "
        "roster record; scans list supported downstream graph nodes."
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
    "PluribusClearedTalent": "Pluribus · Talent Record",
}

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _resolve_data_dir() -> str:
    """Writable directory for invite/action records.

    Cloud-hosted ComfyUI often mounts custom_nodes read-only; fall back from
    PLURIBUS_DATA_DIR -> <plugin>/data -> a per-user temp dir so the plugin
    keeps working instead of crashing on first invite.
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
        "[Pluribus] Plugin directory is not writable; invite records will be "
        f"stored in {fallback} (set PLURIBUS_DATA_DIR to override)."
    )
    return fallback


try:
    from server import PromptServer  # type: ignore[import-not-found]

    from pluribus.server import register_routes

    _data_dir = _resolve_data_dir()
    register_routes(
        PromptServer.instance,
        roster_path=os.path.join(_HERE, "seed", "roster.json"),
        actions_path=os.path.join(_data_dir, "invites.json"),
        connection_path=os.path.join(_data_dir, "connection.json"),
    )
    print("[Pluribus] Rights workflow routes registered.")
except Exception as exc:  # pragma: no cover - ComfyUI runtime only.
    print(f"[Pluribus] Route registration skipped: {exc}")

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
