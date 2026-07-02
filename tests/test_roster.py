import os

from pluribus.roster import Roster

SEED = os.path.join(os.path.dirname(__file__), "..", "seed", "roster.json")


def test_match_is_case_insensitive_by_any_key():
    roster = Roster.from_json(SEED)
    assert roster.match("sarah_chen_id_sdxl.safetensors").name == "Sarah Chen"
    assert roster.match("sarah_ref.png").talent_id == "t_sarah"


def test_restricted_asset_carries_conflicts_and_blake_fields():
    roster = Roster.from_json(SEED)
    elena = roster.match("elena_ref.png")
    assert elena.clearance_status == "restricted"
    assert elena.conflicts
    assert elena.union_status == "SAG-AFTRA"
    assert elena.rep == "WME"


def test_match_returns_none_for_unknown_or_empty():
    roster = Roster.from_json(SEED)
    assert roster.match("stock_crowd_julia.png") is None
    assert roster.match("") is None


def test_replacement_key_matches_source_kind():
    roster = Roster.from_json(SEED)
    assert roster.replacement_key_for("reference").lower().endswith(
        (".png", ".jpg", ".jpeg", ".webp")
    )
    assert roster.replacement_key_for("lora").lower().endswith(".safetensors")
