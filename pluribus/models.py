from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ClearanceState(str, Enum):
    CLEARED = "cleared"
    NEEDS_REVIEW = "needs_review"
    RESTRICTED = "restricted"
    SYNTHETIC_UNVERIFIED = "synthetic_unverified"
    UNIDENTIFIED = "unidentified"


@dataclass
class TalentAsset:
    talent_id: str
    name: str
    asset_keys: list[str]
    clearance_status: str
    scope: str = ""
    allowed_uses: list[str] = field(default_factory=list)
    prohibited_uses: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    union_status: str = ""
    rep: str = ""
    synthetic_only: bool = False
    replacement_asset_key: str = ""


@dataclass
class PersonInstance:
    output_node_id: str
    source_kind: str
    source_key: str
    state: ClearanceState
    source_node_id: str = ""
    talent_id: str | None = None
    name: str | None = None
    note: str = ""
    provenance: list[str] = field(default_factory=list)
    allowed_uses: list[str] = field(default_factory=list)
    prohibited_uses: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    scope: str = ""
    union_status: str = ""
    rep: str = ""
    synthetic_only: bool = False
    replacement_asset_key: str = ""
    # Downstream operations performed on this person's likeness:
    # [{"node_id": str, "class_type": str}], ordered by node id.
    ops: list[dict] = field(default_factory=list)


@dataclass
class ScanResult:
    persons: list[PersonInstance] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        counts = {state.value: 0 for state in ClearanceState}
        for person in self.persons:
            counts[person.state.value] += 1
        return counts
