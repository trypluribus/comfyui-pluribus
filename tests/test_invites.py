import json

from pluribus.invites import read_actions, record_action


def test_invite_route_identify_append_with_distinct_statuses(tmp_path):
    path = tmp_path / "invites.json"
    invited = record_action(
        str(path),
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
    )
    routed = record_action(
        str(path),
        "route",
        talent_id="t_elena",
        name="Elena Vasquez",
        source_key="elena_ref.png",
    )
    identified = record_action(
        str(path),
        "identify",
        talent_id=None,
        name="Unknown",
        source_key="stock_crowd_julia.png",
    )
    assert invited["status"] == "invited"
    assert routed["status"] == "routed_for_review"
    assert identified["status"] == "identification_requested"
    assert [record["kind"] for record in json.loads(path.read_text())] == [
        "invite",
        "route",
        "identify",
    ]


def test_read_actions_empty_when_missing(tmp_path):
    assert read_actions(str(tmp_path / "nope.json")) == []


def test_invite_records_email_note_delivery_and_accept_code(tmp_path):
    path = tmp_path / "invites.json"
    record = record_action(
        str(path),
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        email="marcus@example.com",
        note="Setting your terms for the Morning People campaign.",
        delivery="email",
    )
    assert record["email"] == "marcus@example.com"
    assert record["note"].startswith("Setting your terms")
    assert record["delivery"] == "email"
    assert record["accept_code"].startswith("PL-")
    assert len(record["accept_code"]) == len("PL-XXXX-XXXX")
    assert record["accept_url"].endswith(record["accept_code"])


def test_invite_defaults_to_link_delivery_and_always_gets_code(tmp_path):
    record = record_action(
        str(tmp_path / "invites.json"),
        "invite",
        talent_id=None,
        name="Unknown",
        source_key="ref.png",
    )
    assert record["delivery"] == "link"
    assert record["accept_code"]


def test_non_invite_actions_have_no_accept_fields(tmp_path):
    record = record_action(
        str(tmp_path / "invites.json"),
        "route",
        talent_id="t_elena",
        name="Elena Vasquez",
        source_key="elena_ref.png",
    )
    assert "accept_code" not in record
    assert "email" not in record
