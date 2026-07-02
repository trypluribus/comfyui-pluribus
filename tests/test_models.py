from pluribus.models import ClearanceState, PersonInstance, ScanResult


def test_summary_counts_all_five_states():
    result = ScanResult(
        persons=[
            PersonInstance("9", "lora", "Sarah_Chen_ID_SDXL.safetensors", ClearanceState.CLEARED),
            PersonInstance("13", "reference", "marcus_ref.png", ClearanceState.NEEDS_REVIEW),
            PersonInstance("17", "reference", "elena_ref.png", ClearanceState.RESTRICTED),
            PersonInstance("21", "prompt", "", ClearanceState.SYNTHETIC_UNVERIFIED),
            PersonInstance("25", "reference", "stock_crowd_julia.png", ClearanceState.UNIDENTIFIED),
        ]
    )
    assert result.summary() == {
        "cleared": 1,
        "needs_review": 1,
        "restricted": 1,
        "synthetic_unverified": 1,
        "unidentified": 1,
    }
