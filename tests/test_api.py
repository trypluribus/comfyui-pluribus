import os

import pytest

from pluribus.api import (
    invite_payload,
    packet_payload,
    replace_payload,
    scan_payload,
    scan_request_payload,
    workflow_fingerprint,
)
from pluribus.engine import ClearanceEngine
from pluribus.invites import apply_status_updates, record_action
from pluribus.roster import Roster

SEED = os.path.join(os.path.dirname(__file__), "..", "seed", "roster.json")
WORKFLOW = "Morning People"
REACTOR_SCOPE = [
    "Use of their reference image as a generation source",
    "Use of their face / likeness in this workflow",
]


def _engine():
    return ClearanceEngine(Roster.from_json(SEED))


def _reference_graph(image="marcus_ref.png", operator="ReActorFaceSwap"):
    input_name = "source_image" if operator == "ReActorFaceSwap" else "image"
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": operator, "inputs": {input_name: ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }


def test_scan_payload_serializes_blake_fields_and_state_actions():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    out = scan_payload(api, _engine())
    person = out["persons"][0]
    assert out["summary"]["needs_review"] == 1
    assert person["state"] == "needs_review"
    assert person["name"] == "Marcus Reed"
    assert person["scope"] == ""
    assert person["union_status"] == "non-union"
    assert person["synthetic_only"] is False
    assert person["replacement_asset_key"] == "sarah_ref.png"
    assert person["available_actions"] == ["link", "not_person", "review"]


def test_scan_payload_unidentified_never_invites():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "stock_crowd_julia.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["state"] == "unidentified"
    assert person["available_actions"] == ["link", "not_person", "review"]


def test_scan_payload_synthetic_has_no_invite_or_replacement():
    api = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a prompt-only barista portrait", "clip": ["4", 1]},
        },
        "7": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["7", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["state"] == "synthetic_unverified"
    assert person["available_actions"] == ["link", "not_person", "review"]
    assert person["replacement_asset_key"] == ""


def test_scan_payload_excludes_incomplete_marker_and_returns_local_issue():
    api = {
        "30": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "",
                "display_name": "",
                "note": "",
            },
        },
        "31": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "theo-park-storyboard-reference-v1",
                "display_name": "Theo Park",
                "note": "Supporting runner storyboard reference.",
            },
        },
    }

    out = scan_payload(api, _engine())

    assert [person["name"] for person in out["persons"]] == ["Theo Park"]
    assert out["issues"][0]["node_id"] == "30"
    assert out["issues"][0]["code"] == "incomplete_source_marker"


def test_marker_only_payload_has_no_output_occurrence():
    api = {
        "30": {
            "class_type": "PluribusSourceMarker",
            "inputs": {
                "source_kind": "reference",
                "source_key": "theo-reference.png",
                "display_name": "Theo Park",
                "note": "Reference annotation only.",
            },
        }
    }

    person = scan_payload(api, _engine())["persons"][0]

    assert person["output_node_id"] == "30"
    assert person["source_node_id"] == "30"
    assert person["output_node_ids"] == []
    assert person["source_node_ids"] == ["30"]
    assert person["occurrences"] == []


def test_replace_payload_wraps_updated_workflow():
    api = {"1": {"class_type": "LoadImage", "inputs": {"image": "elena_ref.png"}}}
    out = replace_payload({"workflow": api, "source_key": "elena_ref.png", "new_asset_key": "sarah_ref.png"})
    assert out["workflow"]["1"]["inputs"]["image"] == "sarah_ref.png"


def test_invite_payload_records_and_messages(tmp_path):
    path = str(tmp_path / "invites.json")
    out = invite_payload(
        {"kind": "invite", "name": "Marcus Reed", "source_key": "marcus_ref.png"},
        path,
    )
    assert out["action"]["status"] == "draft"
    assert "accept_code" not in out["action"]
    assert "accept_url" not in out["action"]
    assert out["message"] == (
        "Draft saved locally for Marcus Reed. Connect to Pluribus to send it; "
        "no accept link was created."
    )


def test_packet_payload_returns_json_markdown_and_recorded_actions(tmp_path):
    path = str(tmp_path / "invites.json")
    record_action(path, "invite", talent_id="t_marcus", name="Marcus Reed", source_key="marcus_ref.png")
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    out = packet_payload(api, _engine(), path)
    assert out["packet"]["summary"]["needs_review"] == 1
    assert out["packet"]["actions"][0]["name"] == "Marcus Reed"
    assert "Pluribus Approval Packet" in out["markdown"]
    assert "Actions taken" in out["markdown"]


def test_scan_payload_includes_source_node_id():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["source_node_id"] == "1"


def test_scan_payload_exposes_grouped_source_occurrences_and_legacy_ids():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "actor.png"}},
        "2": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "3": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        "6": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }

    out = scan_payload(api, _engine())

    assert len(out["persons"]) == 1
    person = out["persons"][0]
    assert person["output_node_id"] == "5"
    assert person["source_node_id"] == "1"
    assert person["output_node_ids"] == ["5", "6"]
    assert person["source_node_ids"] == ["1"]
    assert [occurrence["output_node_id"] for occurrence in person["occurrences"]] == [
        "5",
        "6",
    ]
    assert [operation["node_id"] for operation in person["ops"]] == ["1", "2", "3"]
    assert out["summary"]["unidentified"] == 1


def test_scan_payload_derives_video_reference_scope_for_runway_edit():
    api = {
        "10": {"class_type": "LoadVideo", "inputs": {"file": "lf_night_s02.mp4"}},
        "11": {
            "class_type": "RunwayAleph2VideoToVideoNode",
            "inputs": {"video": ["10", 0], "prompt": "restyle the girl's scene"},
        },
        "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0]}},
    }

    person = scan_payload(api, _engine())["persons"][0]

    assert person["source_kind"] == "reference"
    assert person["source_key"] == "lf_night_s02.mp4"
    assert person["scope_statements"] == [
        "Use of their source video as a generation source",
        "AI editing of their source video",
    ]


def test_scan_payload_scope_is_source_specific_at_multimodal_join():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
        "2": {"class_type": "FluxKontextProImageNode", "inputs": {"input_image": ["1", 0]}},
        "3": {"class_type": "LoadVideo", "inputs": {"file": "motion.mp4"}},
        "4": {"class_type": "RunwayAleph2VideoToVideoNode", "inputs": {"video": ["3", 0]}},
        "5": {"class_type": "LoadAudio", "inputs": {"audio": "voice.m4a"}},
        "6": {
            "class_type": "ByteDance2ReferenceNode",
            "inputs": {
                "model.reference_images.image_1": ["2", 0],
                "model.reference_videos.video_1": ["4", 0],
                "model.reference_audios.audio_1": ["5", 0],
            },
        },
        "9": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0]}},
    }

    by_key = {
        person["source_key"]: person for person in scan_payload(api, _engine())["persons"]
    }

    assert by_key["person.png"]["scope_statements"] == [
        "Use of their reference image as a generation source",
        "AI editing of their reference image",
        "Conditioning AI generation on their reference image / likeness",
    ]
    assert by_key["motion.mp4"]["scope_statements"] == [
        "Use of their source video as a generation source",
        "AI editing of their source video",
        "Conditioning AI generation on their source video / performance",
    ]
    assert by_key["voice.m4a"]["scope_statements"] == [
        "Use of their source audio / voice performance as a generation source",
        "Conditioning AI generation on their voice / audio performance",
    ]


def test_scan_payload_ops_include_manifest_bound_source_operations_and_roles():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
        "2": {"class_type": "LoadVideo", "inputs": {"file": "motion.mp4"}},
        "3": {"class_type": "LoadAudio", "inputs": {"audio": "voice.m4a"}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.safetensors"}},
        "5": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "performer.safetensors", "model": ["4", 0]},
        },
        "6": {
            "class_type": "ByteDance2ReferenceNode",
            "inputs": {
                "model": ["5", 0],
                "model.reference_images.image_1": ["1", 0],
                "model.reference_videos.video_1": ["2", 0],
                "model.reference_audios.audio_1": ["3", 0],
            },
        },
        "9": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0]}},
    }

    by_key = {
        person["source_key"]: person for person in scan_payload(api, _engine())["persons"]
    }

    assert by_key["performer.safetensors"]["ops"][0] == {
        "node_id": "5",
        "class_type": "LoraLoader",
    }
    assert by_key["person.png"]["ops"][0] == {
        "node_id": "1",
        "class_type": "LoadImage",
        "source_role": "reference_image",
    }
    assert by_key["motion.mp4"]["ops"][0] == {
        "node_id": "2",
        "class_type": "LoadVideo",
        "source_role": "reference_video",
    }
    assert by_key["voice.m4a"]["ops"][0] == {
        "node_id": "3",
        "class_type": "LoadAudio",
        "source_role": "reference_audio",
    }


def test_action_payload_disconnected_invite_carries_email_but_no_accept_url(tmp_path):
    from pluribus.api import action_payload

    out = action_payload(
        {
            "kind": "invite",
            "talent_id": "t_marcus",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
            "email": "marcus@example.com",
            "note": "hi",
            "delivery": "email",
        },
        str(tmp_path / "invites.json"),
    )
    assert out["action"]["email"] == "marcus@example.com"
    assert out["action"]["status"] == "draft"
    assert out["action"]["requested_delivery"] == "email"
    assert "accept_code" not in out["action"]
    assert "accept_url" not in out["action"]
    assert "no accept link was created" in out["message"]


def test_action_payload_connected_invite_uses_server_link_and_marks_invited(tmp_path):
    from pluribus.api import action_payload

    out = action_payload(
        {
            "kind": "invite",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
            "email": "marcus@example.com",
            "delivery": "email",
        },
        str(tmp_path / "invites.json"),
        remote_result={
            "state": "sent",
            "status": "sent",
            "accept_code": "PL-AAAA-BBBB",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB",
            "email_delivery": "sent",
            "invite_id": "inv-1",
        },
    )

    assert out["action"]["status"] == "invited"
    assert out["action"]["accept_code"] == "PL-AAAA-BBBB"
    assert out["action"]["accept_url"].endswith("/PL-AAAA-BBBB")
    assert out["message"] == "Invite emailed to marcus@example.com via Pluribus."


def test_action_payload_malformed_server_success_downgrades_to_linkless_draft(tmp_path):
    from pluribus.api import action_payload

    out = action_payload(
        {
            "kind": "invite",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
        },
        str(tmp_path / "invites.json"),
        remote_result={"state": "sent", "status": "sent"},
    )

    assert out["action"]["status"] == "draft"
    assert out["action"]["draft_reason"] == "unconfirmed"
    assert "accept_code" not in out["action"]
    assert "accept_url" not in out["action"]
    assert "may already have been created or emailed" in out["message"]
    assert "same request" in out["message"]


@pytest.mark.parametrize(
    ("remote_state", "draft_reason"),
    [("unconfirmed", "unconfirmed"), ("unauthorized", "unauthorized"), ("error", "error")],
)
def test_action_payload_remote_failure_is_only_a_draft(
    tmp_path, remote_state, draft_reason
):
    from pluribus.api import action_payload

    out = action_payload(
        {
            "kind": "invite",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
            "delivery": "link",
        },
        str(tmp_path / f"{remote_state}.json"),
        remote_result={"state": remote_state, "message": "boom"},
    )

    assert out["action"]["status"] == "draft"
    assert out["action"]["draft_reason"] == draft_reason
    assert "accept_code" not in out["action"]
    assert "accept_url" not in out["action"]
    if remote_state == "unconfirmed":
        assert "same request" in out["message"]
        assert "Nothing was sent" not in out["message"]
    else:
        assert "draft saved locally" in out["message"]
        assert "no accept link was created" in out["message"]


@pytest.mark.parametrize("attempt_state", ["ambiguous", "in_flight"])
def test_action_payload_email_ambiguity_retries_same_canonical_invite(
    tmp_path, attempt_state
):
    from pluribus.api import action_payload

    out = action_payload(
        {
            "kind": "invite",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
            "email": "marcus@example.com",
            "delivery": "email",
        },
        str(tmp_path / f"{attempt_state}.json"),
        remote_result={
            "state": "sent",
            "status": "sent",
            "accept_code": "PL-AAAA-BBBB",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB",
            "email_delivery": "failed",
            "email_attempt_state": attempt_state,
            "email_attempt_started_at": "2026-07-11T15:00:00Z",
            "email_reconciliation_required": False,
            "invite_id": "inv-1",
        },
    )

    assert out["action"]["status"] == "invited"
    assert out["action"]["email_attempt_state"] == attempt_state
    assert out["action"]["email_attempt_started_at"] == "2026-07-11T15:00:00Z"
    assert out["action"]["email_reconciliation_required"] is False
    assert "email may already have been sent" in out["message"]
    assert "same invite" in out["message"]
    assert "share the accept link" not in out["message"]


def test_action_payload_manual_email_reconciliation_forbids_automatic_resend(tmp_path):
    from pluribus.api import action_payload

    out = action_payload(
        {
            "kind": "invite",
            "name": "Marcus Reed",
            "source_key": "marcus_ref.png",
            "email": "marcus@example.com",
            "delivery": "email",
        },
        str(tmp_path / "manual.json"),
        remote_result={
            "state": "sent",
            "status": "sent",
            "accept_code": "PL-AAAA-BBBB",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB",
            "email_delivery": "failed",
            "email_attempt_state": "manual_reconciliation",
            "email_attempt_started_at": "2026-07-10T12:00:00Z",
            "email_reconciliation_required": True,
            "invite_id": "inv-1",
        },
    )

    assert out["action"]["status"] == "invited"
    assert out["action"]["email_reconciliation_required"] is True
    assert "Do not resend automatically" in out["message"]
    assert "operator/provider review" in out["message"]


def test_action_payload_retry_reuses_request_id_and_upserts_draft(tmp_path):
    from pluribus.api import action_payload
    from pluribus.invites import read_actions

    path = str(tmp_path / "invites.json")
    body = {
        "kind": "invite",
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "scope_statements": REACTOR_SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    first = action_payload(body, path, remote_result={"state": "unconfirmed"})
    retry = action_payload(
        body,
        path,
        remote_result={
            "state": "sent",
            "accept_code": "PL-AAAA-BBBB",
            "accept_url": "https://trypluribus.com/accept/PL-AAAA-BBBB",
            "email_delivery": "sent",
            "status": "sent",
            "invite_id": "inv-1",
        },
    )

    assert retry["action"]["client_request_id"] == first["action"]["client_request_id"]
    assert retry["action"]["status"] == "invited"
    assert len(read_actions(path)) == 1


def test_explicit_retry_request_id_survives_intervening_sync_hydration(tmp_path):
    from pluribus.api import action_payload
    from pluribus.invites import read_actions

    path = str(tmp_path / "invites.json")
    body = {
        "kind": "invite",
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": WORKFLOW,
        "workflow_fingerprint": "a" * 64,
        "scope_statements": REACTOR_SCOPE,
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    first = action_payload(body, path, remote_result={"state": "unconfirmed"})
    request_id = first["action"]["client_request_id"]
    apply_status_updates(
        path,
        [
            {
                "id": "inv-1",
                "clientRequestId": request_id,
                "acceptCode": "PL-AAAA-BBBB",
                "status": "sent",
                "emailDelivery": "sent",
            }
        ],
    )

    retry = action_payload(
        {**body, "client_request_id": request_id},
        path,
        remote_result={
            "state": "sent",
            "accept_code": "PL-AAAA-BBBB",
            "email_delivery": "sent",
            "status": "sent",
            "invite_id": "inv-1",
        },
    )

    assert retry["action"]["client_request_id"] == request_id
    assert len(read_actions(path)) == 1


def test_roster_payload_lists_all_talent():
    from pluribus.api import roster_payload

    out = roster_payload(_engine())
    names = [t["name"] for t in out["talent"]]
    assert "Sarah Chen" in names
    assert "Elena Vasquez" in names
    sarah = next(t for t in out["talent"] if t["name"] == "Sarah Chen")
    assert sarah["clearance_status"] == "cleared"
    assert sarah["asset_keys"]


def test_scan_payload_serializes_ops():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    person = scan_payload(api, _engine())["persons"][0]
    assert person["ops"] == [
        {
            "node_id": "1",
            "class_type": "LoadImage",
            "source_role": "reference_image",
        },
        {
            "node_id": "2",
            "class_type": "ReActorFaceSwap",
            "input_name": "source_image",
            "source_role": "reference_image",
        }
    ]


def test_scan_payload_reads_accepted_invite_each_time_and_removes_invite_action(tmp_path):
    path = str(tmp_path / "invites.json")
    api = _reference_graph()
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        workflow_fingerprint=workflow_fingerprint(api),
        scope_statements=REACTOR_SCOPE,
        override={
            "accept_code": "PL-AAAA-BBBB",
            "email_delivery": "sent",
            "status": "sent",
        },
    )
    apply_status_updates(
        path,
        [
            {
                "acceptCode": "PL-AAAA-BBBB",
                "status": "accepted",
                "acceptedAt": "2026-07-04T10:00:00Z",
            }
        ],
    )
    first = scan_payload(api, _engine(), path, workflow_name=WORKFLOW)["persons"][0]
    second = scan_payload(api, _engine(), path, workflow_name=WORKFLOW)["persons"][0]

    assert first["state"] == "needs_review"
    assert first["terms_status"] == "accepted"
    assert first["terms_accepted_at"] == "2026-07-04T10:00:00Z"
    assert first["available_actions"] == ["link", "not_person", "review"]
    assert second["terms_status"] == "accepted"
    assert "invite" not in second["available_actions"]


def test_scan_payload_draft_does_not_look_accepted_or_remove_invite(tmp_path):
    path = str(tmp_path / "invites.json")
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        draft_reason="offline",
    )
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }

    person = scan_payload(api, _engine(), path)["persons"][0]

    assert person["terms_status"] is None
    assert person["terms_accepted_at"] is None
    assert person["available_actions"] == ["link", "not_person", "review"]


def test_scan_payload_derives_canonical_scope_statements_from_graph():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }

    person = scan_payload(api, _engine(), workflow_name=WORKFLOW)["persons"][0]

    assert person["scope_statements"] == REACTOR_SCOPE
    assert person["workflow_name"] == WORKFLOW
    assert person["workflow_fingerprint"] == workflow_fingerprint(api)


def test_workflow_fingerprint_uses_canonical_utf8_graph_json():
    graph = {
        "2": {
            "inputs": {"z": "é", "a": [1, True, None]},
            "class_type": "X",
        },
        "1": {"inputs": {}, "class_type": "Y"},
    }
    reordered = {"1": graph["1"], "2": graph["2"]}

    assert workflow_fingerprint(graph) == workflow_fingerprint(reordered)
    assert workflow_fingerprint(graph) == (
        "f2c2de32c325a4c0b15f4232a4794923d6fc11c623697d3510510bf90ca4924f"
    )


def test_wrapped_scan_preserves_js_fingerprint_for_float_interoperability():
    api = {
        "2": {
            "inputs": {
                "strength": 1.0,
                "ratio": 0.5,
                "label": "é",
                "flags": [True, False, None],
                "nested": {"z": 2.0, "a": "x"},
            },
            "class_type": "X",
        },
        "1": {"inputs": {}, "class_type": "Y"},
    }
    js_fingerprint = "f1eafde7905ff8c0cebf3a84d7ba45651441afd6e2f8411e877e3ad4414561ff"

    result = scan_request_payload(
        {
            "workflow": api,
            "workflow_name": WORKFLOW,
            "workflow_fingerprint": js_fingerprint,
        },
        _engine(),
    )

    assert workflow_fingerprint(api) != js_fingerprint
    assert result["workflow_fingerprint"] == js_fingerprint


def test_scan_request_accepts_wrapped_context_and_legacy_raw_graph():
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }

    wrapped = scan_request_payload(
        {
            "workflow": api,
            "workflow_name": WORKFLOW,
            "workflow_fingerprint": "a" * 64,
        },
        _engine(),
    )
    legacy = scan_request_payload(api, _engine())

    assert wrapped["persons"][0]["name"] == "Marcus Reed"
    assert wrapped["workflow_name"] == WORKFLOW
    assert wrapped["workflow_fingerprint"] == "a" * 64
    assert wrapped["persons"][0]["workflow_fingerprint"] == "a" * 64
    assert legacy["persons"][0]["name"] == "Marcus Reed"


def test_accepted_terms_do_not_cross_workflow_boundary(tmp_path):
    path = str(tmp_path / "invites.json")
    api = _reference_graph()
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        source_kind="reference",
        workflow_name="Workflow A",
        workflow_fingerprint=workflow_fingerprint(api),
        scope_statements=REACTOR_SCOPE,
        override={"accept_code": "PL-AAAA-BBBB", "status": "accepted"},
    )
    accepted = scan_payload(api, _engine(), path, workflow_name="Workflow A")["persons"][0]
    other = scan_payload(api, _engine(), path, workflow_name="Workflow B")["persons"][0]

    assert accepted["terms_status"] == "accepted"
    assert "invite" not in accepted["available_actions"]
    assert other["terms_status"] is None
    assert other["available_actions"] == ["link", "not_person", "review"]


def test_accepted_terms_do_not_cross_scope_change_in_same_workflow(tmp_path):
    path = str(tmp_path / "invites.json")
    reactor = _reference_graph()
    adapter = _reference_graph(operator="IPAdapter")
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        workflow_fingerprint=workflow_fingerprint(reactor),
        scope_statements=REACTOR_SCOPE,
        override={"accept_code": "PL-AAAA-BBBB", "status": "accepted"},
    )
    original = scan_payload(reactor, _engine(), path, workflow_name=WORKFLOW)["persons"][0]
    changed = scan_payload(adapter, _engine(), path, workflow_name=WORKFLOW)["persons"][0]

    assert original["terms_status"] == "accepted"
    assert changed["scope_statements"] != REACTOR_SCOPE
    assert changed["terms_status"] is None
    assert changed["available_actions"] == ["link", "not_person", "review"]


def test_any_api_graph_change_strictly_invalidates_accepted_terms(tmp_path):
    path = str(tmp_path / "invites.json")
    original = _reference_graph()
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        workflow_fingerprint=workflow_fingerprint(original),
        scope_statements=REACTOR_SCOPE,
        override={"accept_code": "PL-AAAA-BBBB", "status": "accepted"},
    )
    changed = _reference_graph()
    changed["9"]["inputs"]["filename_prefix"] = "changed-output"

    before = scan_payload(original, _engine(), path, workflow_name=WORKFLOW)["persons"][0]
    after = scan_payload(changed, _engine(), path, workflow_name=WORKFLOW)["persons"][0]

    assert before["scope_statements"] == after["scope_statements"]
    assert before["terms_status"] == "accepted"
    assert after["terms_status"] is None
    assert after["available_actions"] == ["link", "not_person", "review"]


def test_pre_fingerprint_accepted_record_is_conservatively_not_displayed(tmp_path):
    path = str(tmp_path / "invites.json")
    api = _reference_graph()
    record_action(
        path,
        "invite",
        talent_id="t_marcus",
        name="Marcus Reed",
        source_key="marcus_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        scope_statements=REACTOR_SCOPE,
        override={"accept_code": "PL-OLD-0001", "status": "accepted"},
    )

    person = scan_payload(api, _engine(), path, workflow_name=WORKFLOW)["persons"][0]

    assert person["terms_status"] is None
    assert person["available_actions"] == ["link", "not_person", "review"]


def test_accepted_terms_do_not_replace_restricted_roster_state(tmp_path):
    path = str(tmp_path / "invites.json")
    api = _reference_graph(image="elena_ref.png")
    record_action(
        path,
        "invite",
        talent_id="t_elena",
        name="Elena Vasquez",
        source_key="elena_ref.png",
        source_kind="reference",
        workflow_name=WORKFLOW,
        workflow_fingerprint=workflow_fingerprint(api),
        scope_statements=REACTOR_SCOPE,
        override={"accept_code": "PL-ELENA-01", "status": "accepted"},
    )
    person = scan_payload(api, _engine(), path, workflow_name=WORKFLOW)["persons"][0]

    assert person["state"] == "restricted"
    assert person["terms_status"] == "accepted"
    assert person["available_actions"] == ["link", "not_person", "review"]


def test_validation_error_does_not_write_local_draft(tmp_path):
    from pluribus.api import action_payload

    path = str(tmp_path / "invites.json")
    out = action_payload(
        {"kind": "invite", "name": "Marcus Reed", "delivery": "email"},
        path,
        remote_result={
            "state": "validation_error",
            "message": "Enter a valid email address for email delivery.",
        },
    )

    assert out["state"] == "validation_error"
    assert out["action"] is None
    assert not os.path.exists(path)
