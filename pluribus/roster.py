from __future__ import annotations

import json

from .models import TalentAsset

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


class Roster:
    def __init__(self, assets: list[TalentAsset]):
        self._assets = assets
        self._by_key: dict[str, TalentAsset] = {}
        for asset in assets:
            for key in asset.asset_keys:
                self._by_key[key.lower()] = asset

    @classmethod
    def from_json(cls, path: str) -> "Roster":
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return cls([TalentAsset(**row) for row in data["talent"]])

    def match(self, key: str) -> TalentAsset | None:
        if not key:
            return None
        return self._by_key.get(key.lower())

    def match_name(self, name: str) -> TalentAsset | None:
        if not name:
            return None
        needle = name.strip().lower()
        for asset in self._assets:
            if asset.name.lower() == needle:
                return asset
        return None

    def all_assets(self) -> list[TalentAsset]:
        return list(self._assets)

    def cleared_assets(self) -> list[TalentAsset]:
        return [asset for asset in self._assets if asset.clearance_status == "cleared"]

    def replacement_key_for(self, kind: str) -> str:
        """Return a cleared asset compatible with the source field kind."""
        if kind == "lora":
            is_compatible = lambda key: key.lower().endswith(".safetensors")
        else:
            is_compatible = lambda key: key.lower().endswith(IMAGE_EXTENSIONS)

        for asset in self.cleared_assets():
            candidates = [asset.replacement_asset_key, *asset.asset_keys]
            for key in candidates:
                if key and is_compatible(key):
                    return key
        return ""
