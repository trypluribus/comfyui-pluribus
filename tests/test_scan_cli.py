import os

from pluribus.scan_cli import run

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "morning_people_spot_workflow_api.json"
)
SEED = os.path.join(os.path.dirname(__file__), "..", "seed", "roster.json")


def test_cli_reports_all_five_states_and_writes_packet(tmp_path):
    summary = run(FIXTURE, SEED, str(tmp_path))
    assert summary == {
        "cleared": 1,
        "needs_review": 1,
        "restricted": 1,
        "synthetic_unverified": 1,
        "unidentified": 1,
    }
    packet_path = os.path.join(str(tmp_path), "approval_packet.md")
    assert os.path.exists(packet_path)
    packet = open(packet_path, encoding="utf-8").read()
    assert "not legal clearance" in packet.lower()
    assert "Replacement asset key" in packet
