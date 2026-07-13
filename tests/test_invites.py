import json
import uuid

import pytest

from pluribus.invites import (
    client_request_id_for_invite,
    latest_accepted_invite,
    read_actions,
    record_action,
)

WORKFLOW = "Morning People"
FINGERPRINT = "a" * 64
SCOPE = ["Use of their reference image as a generation source"]


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
    assert invited["status"] == "draft"
    assert routed["status"] == "routed_for_review"
    assert identified["status"] == "identification_requested"
    assert [record["kind"] for record in json.loads(path.read_text())] == [
        "invite",
        "route",
        "identify",
    ]


def test_read_actions_empty_when_missing(tmp_path):
    assert read_actions(str(tmp_path / "nope.json")) == []


def test_disconnected_invite_records_local_draft_without_accept_link(tmp_path):
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
    assert record["status"] == "draft"
    assert record["requested_delivery"] == "email"
    assert record["draft_reason"] == "disconnected"
    assert record["synced"] is False
    assert "delivery" not in record
    assert "accept_code" not in record
    assert "accept_url" not in record


def test_local_draft_can_record_failure_reason_without_creating_bearer_secret(tmp_path):
    record = record_action(
        str(tmp_path / "invites.json"),
        "invite",
        talent_id=None,
        name="Unknown",
        source_key="ref.png",
        delivery="link",
        draft_reason="offline",
    )
    assert record["status"] == "draft"
    assert record["requested_delivery"] == "link"
    assert record["draft_reason"] == "offline"
    assert "accept_code" not in record
    assert "accept_url" not in record


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


def test_record_action_with_server_override_uses_canonical_code(tmp_path):
    path = str(tmp_path / "invites.json")
    record = record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        email="marcus@example.com",
        delivery="email",
        override={
            "accept_code": "PL-AAAA-BBBB",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB",
            "email_delivery": "sent",
            "email_attempt_state": "sent",
            "email_attempt_started_at": "2026-07-11T15:00:00Z",
            "email_reconciliation_required": False,
        },
    )
    assert record["accept_code"] == "PL-AAAA-BBBB"
    assert record["accept_url"].endswith("/accept/PL-AAAA-BBBB")
    assert record["status"] == "invited"
    assert record["synced"] is True
    assert record["email_delivery"] == "sent"
    assert record["email_attempt_state"] == "sent"
    assert record["email_attempt_started_at"] == "2026-07-11T15:00:00Z"
    assert record["email_reconciliation_required"] is False
    assert record["server_status"] == "sent"


def test_record_action_without_override_is_draft_only(tmp_path):
    path = str(tmp_path / "invites.json")
    record = record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        email="marcus@example.com",
    )
    assert record["synced"] is False
    assert record["status"] == "draft"
    assert "accept_code" not in record
    assert "accept_url" not in record


def test_apply_status_updates_merges_by_accept_code(tmp_path):
    from pluribus.invites import apply_status_updates

    path = str(tmp_path / "invites.json")
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        override={"accept_code": "PL-AAAA-BBBB", "email_delivery": "sent"},
    )
    changed = apply_status_updates(
        path,
        [
            {
                "acceptCode": "PL-AAAA-BBBB",
                "status": "accepted",
                "emailDelivery": "failed",
                "emailAttemptState": "manual_reconciliation",
                "emailAttemptStartedAt": "2026-07-10T12:00:00Z",
                "emailReconciliationRequired": True,
                "acceptedAt": "2026-07-04T10:00:00Z",
            },
            {"acceptCode": "PL-ZZZZ-ZZZZ", "status": "sent"},
        ],
    )
    assert changed == 1
    records = read_actions(path)
    assert records[0]["server_status"] == "accepted"
    assert records[0]["accepted_at"] == "2026-07-04T10:00:00Z"
    assert records[0]["email_delivery"] == "failed"
    assert records[0]["email_attempt_state"] == "manual_reconciliation"
    assert records[0]["email_attempt_started_at"] == "2026-07-10T12:00:00Z"
    assert records[0]["email_reconciliation_required"] is True

    # Idempotent: same payload again changes nothing.
    assert apply_status_updates(path, [{"acceptCode": "PL-AAAA-BBBB", "status": "accepted", "acceptedAt": "2026-07-04T10:00:00Z"}]) == 0


def test_latest_accepted_invite_matches_source_key_and_ignores_drafts(tmp_path):
    path = str(tmp_path / "invites.json")
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
    )
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="alternate_marcus_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        scope_statements=SCOPE,
        override={
            "accept_code": "PL-ALT-0001",
            "status": "accepted",
        },
    )
    accepted = record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        scope_statements=SCOPE,
        override={
            "accept_code": "PL-MAIN-0001",
            "status": "accepted",
        },
    )

    actions = read_actions(path)
    assert latest_accepted_invite(
        actions,
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        source_key="marcus_ref.png",
        source_kind="reference",
        scope_statements=SCOPE,
        talent_id="t_marcus",
    ) == accepted
    assert latest_accepted_invite(
        actions,
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        source_key="unrelated.png",
        source_kind="reference",
        scope_statements=SCOPE,
        talent_id="t_marcus",
    ) is None


def test_latest_accepted_invite_falls_back_to_talent_id_without_source_key():
    accepted = {
        "kind": "invite",
        "synced": True,
        "server_status": "accepted",
        "talent_id": "t_marcus",
        "source_key": "",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
    }
    assert latest_accepted_invite(
        [accepted],
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        source_key="",
        source_kind="reference",
        scope_statements=SCOPE,
        talent_id="t_marcus",
    ) == accepted


def test_latest_accepted_invite_ignores_unsynced_local_status():
    local = {
        "kind": "invite",
        "synced": False,
        "server_status": "accepted",
        "talent_id": "t_marcus",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
    }
    assert latest_accepted_invite(
        [local],
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        source_key="marcus_ref.png",
        source_kind="reference",
        scope_statements=SCOPE,
        talent_id="t_marcus",
    ) is None


def test_latest_accepted_invite_ignores_pre_upgrade_record_without_fingerprint():
    accepted = {
        "kind": "invite",
        "synced": True,
        "server_status": "accepted",
        "talent_id": "t_marcus",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "scope_statements": SCOPE,
    }

    assert latest_accepted_invite(
        [accepted],
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        source_key="marcus_ref.png",
        source_kind="reference",
        scope_statements=SCOPE,
        talent_id="t_marcus",
    ) is None


def test_identical_local_draft_retry_reuses_uuid_and_upserts(tmp_path):
    path = str(tmp_path / "invites.json")
    fields = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
        "draft_reason": "unconfirmed",
    }

    first = record_action(path, "invite", **fields)
    second = record_action(path, "invite", **fields)

    assert uuid.UUID(first["client_request_id"])
    assert second["client_request_id"] == first["client_request_id"]
    assert len(read_actions(path)) == 1


def test_changed_invite_fields_get_a_new_client_request_id(tmp_path):
    path = str(tmp_path / "invites.json")
    base = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    first = record_action(path, "invite", **base)
    variants = [
        {"name": "Marcus Reed Jr."},
        {"source_key": "other_ref.png"},
        {"workflow_name": "Another workflow"},
        {"workflow_fingerprint": "b" * 64},
        {"email": "other@example.com"},
        {"note": "Updated note"},
        {"delivery": "link"},
        {"scope_statements": ["A different requested use"]},
    ]

    for variant in variants:
        changed = record_action(path, "invite", **{**base, **variant})
        assert changed["client_request_id"] != first["client_request_id"]
    assert len(read_actions(path)) == len(variants) + 1


def test_successful_retry_upserts_local_draft_to_invited(tmp_path):
    path = str(tmp_path / "invites.json")
    body = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    draft = record_action(path, "invite", **body, draft_reason="unconfirmed")
    request_id = client_request_id_for_invite(path, body)
    invited = record_action(
        path,
        "invite",
        **body,
        client_request_id=request_id,
        override={
            "accept_code": "PL-AAAA-BBBB",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB",
            "email_delivery": "sent",
            "status": "sent",
            "invite_id": "inv-1",
        },
    )

    assert request_id == draft["client_request_id"]
    assert invited["status"] == "invited"
    assert invited["client_request_id"] == request_id
    assert invited["source_kind"] == "reference"
    assert invited["workflow_name"] == WORKFLOW
    assert invited["workflow_fingerprint"] == FINGERPRINT
    assert invited["scope_statements"] == SCOPE
    assert "draft_reason" not in invited
    assert len(read_actions(path)) == 1


@pytest.mark.parametrize(
    ("attempt_state", "reconciliation_required"),
    [
        ("ambiguous", False),
        ("in_flight", False),
        ("manual_reconciliation", True),
    ],
)
def test_provider_ambiguous_invite_retry_after_reload_reuses_uuid(
    tmp_path, attempt_state, reconciliation_required
):
    path = str(tmp_path / "invites.json")
    body = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    original_request_id = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4"
    invited = record_action(
        path,
        "invite",
        **body,
        client_request_id=original_request_id,
        override={
            "accept_code": "PL-AAAA-BBBB-CCCC-DDDD",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB-CCCC-DDDD",
            "email_delivery": "failed",
            "email_attempt_state": attempt_state,
            "email_reconciliation_required": reconciliation_required,
            "status": "sent",
            "invite_id": "inv-1",
        },
    )

    # A reopened browser dialog supplies a fresh UUID. The persisted
    # provider-ambiguous record must win so the server and Resend key replay.
    selected = client_request_id_for_invite(
        path,
        {
            **body,
            "client_request_id": "5bde7ce8-a4ef-4ec5-a627-042e8f66d496",
        },
    )

    assert invited["status"] == "invited"
    assert selected == original_request_id


def test_confirmed_invite_does_not_capture_a_new_identical_request(tmp_path):
    path = str(tmp_path / "invites.json")
    body = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    record_action(
        path,
        "invite",
        **body,
        client_request_id="6ccfc0e8-094a-4d69-a8df-70c864a1e9f4",
        override={
            "accept_code": "PL-AAAA-BBBB-CCCC-DDDD",
            "email_delivery": "sent",
            "email_attempt_state": "sent",
            "status": "sent",
            "invite_id": "inv-1",
        },
    )
    new_request_id = "5bde7ce8-a4ef-4ec5-a627-042e8f66d496"

    assert client_request_id_for_invite(
        path, {**body, "client_request_id": new_request_id}
    ) == new_request_id


def test_transport_failure_retry_preserves_existing_canonical_invite(tmp_path):
    path = str(tmp_path / "invites.json")
    body = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    request_id = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4"
    canonical = record_action(
        path,
        "invite",
        **body,
        client_request_id=request_id,
        override={
            "accept_code": "PL-AAAA-BBBB-CCCC-DDDD",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB-CCCC-DDDD",
            "email_delivery": "failed",
            "email_attempt_state": "ambiguous",
            "status": "sent",
            "invite_id": "inv-1",
        },
    )

    retried = record_action(
        path,
        "invite",
        **body,
        client_request_id=request_id,
        draft_reason="unconfirmed",
        delivery_result={"state": "unconfirmed"},
    )

    assert retried["status"] == "invited"
    assert retried["accept_code"] == canonical["accept_code"]
    assert retried["accept_url"] == canonical["accept_url"]
    assert retried["server_invite_id"] == "inv-1"
    assert retried["email_attempt_state"] == "ambiguous"
    assert retried["client_request_id"] == request_id
    assert retried["last_retry_state"] == "unconfirmed"
    assert len(read_actions(path)) == 1


@pytest.mark.parametrize("later_reason", ["error", "unauthorized"])
def test_definite_retry_failure_does_not_erase_prior_unconfirmed_state(
    tmp_path, later_reason
):
    path = str(tmp_path / "invites.json")
    body = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": FINGERPRINT,
        "scope_statements": SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    uncertain = record_action(
        path,
        "invite",
        **body,
        draft_reason="unconfirmed",
    )

    retried = record_action(
        path,
        "invite",
        **body,
        client_request_id=uncertain["client_request_id"],
        draft_reason=later_reason,
        delivery_result={"state": later_reason},
    )

    assert retried["status"] == "draft"
    assert retried["draft_reason"] == "unconfirmed"
    assert retried["last_retry_state"] == later_reason
    assert retried["client_request_id"] == uncertain["client_request_id"]
    assert len(read_actions(path)) == 1


def test_sync_hydrates_unconfirmed_draft_by_client_request_id(tmp_path):
    from pluribus.invites import apply_status_updates

    path = str(tmp_path / "invites.json")
    draft = record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        workflow_fingerprint=FINGERPRINT,
        scope_statements=SCOPE,
        delivery="link",
        draft_reason="unconfirmed",
    )
    server = {
        "id": "inv-1",
        "clientRequestId": draft["client_request_id"],
        "acceptCode": "PL-AAAA-BBBB",
        "acceptUrl": "https://trypluribus.com/accept/PL-AAAA-BBBB",
        "status": "accepted",
        "emailDelivery": "skipped",
        "emailAttemptState": "not_attempted",
        "emailAttemptStartedAt": None,
        "emailReconciliationRequired": False,
        "acceptedAt": "2026-07-11T10:00:00Z",
    }

    assert apply_status_updates(path, [server]) == 1
    hydrated = read_actions(path)[0]
    assert hydrated["status"] == "invited"
    assert hydrated["synced"] is True
    assert hydrated["server_status"] == "accepted"
    assert hydrated["server_invite_id"] == "inv-1"
    assert hydrated["accept_code"] == "PL-AAAA-BBBB"
    assert hydrated["accept_url"].endswith("/PL-AAAA-BBBB")
    assert hydrated["accepted_at"] == "2026-07-11T10:00:00Z"
    assert hydrated["email_attempt_state"] == "not_attempted"
    assert hydrated["email_attempt_started_at"] is None
    assert hydrated["email_reconciliation_required"] is False
    assert "draft_reason" not in hydrated
    assert "requested_delivery" not in hydrated
    assert apply_status_updates(path, [server]) == 0
