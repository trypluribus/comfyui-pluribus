import asyncio
import json
import os
import uuid

import pytest

from pluribus import remote


@pytest.fixture(autouse=True)
def _clean_pending():
    remote.reset_pending_for_tests()
    yield
    remote.reset_pending_for_tests()


def make_fetch(responses):
    """Fake fetch returning queued (status, data) tuples and logging calls."""
    calls = []
    queue = list(responses)

    async def fetch(method, url, payload=None, token=None):
        calls.append({"method": method, "url": url, "payload": payload, "token": token})
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    fetch.calls = calls
    return fetch


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def connection_path(tmp_path):
    return str(tmp_path / "connection.json")


def test_default_fetch_allows_production_workflow_writes_to_finish(monkeypatch):
    import aiohttp

    captured = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def json(self):
            return {"saved": True}

    class FakeSession:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def request(self, method, url, *, json, headers):
            captured["request"] = {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
            }
            return FakeResponse()

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)

    status, data = run(
        remote._default_fetch(
            "PUT",
            "https://trypluribus.com/api/plugin/projects/project-1/use",
            {"usageType": "advertising"},
            "plt_token",
        )
    )

    assert (status, data) == (200, {"saved": True})
    assert captured["timeout"].total == 30
    assert captured["request"] == {
        "method": "PUT",
        "url": "https://trypluribus.com/api/plugin/projects/project-1/use",
        "json": {"usageType": "advertising"},
        "headers": {"Authorization": "Bearer plt_token"},
    }


def test_status_disconnected_by_default(tmp_path):
    status = remote.get_status(connection_path(tmp_path))
    assert status["state"] == "disconnected"
    assert status["server_url"]


def test_start_pairing_stores_pending_and_returns_code(tmp_path):
    fetch = make_fetch(
        [
            (
                200,
                {
                    "deviceCode": "pld_secret",
                    "userCode": "7Q2M-4KXR",
                    "verificationUrl": "https://trypluribus.com/pair",
                    "intervalSeconds": 5,
                },
            )
        ]
    )

    result = run(remote.start_pairing(fetch=fetch))

    assert result["state"] == "pairing"
    assert result["user_code"] == "7Q2M-4KXR"
    assert fetch.calls[0]["url"].endswith("/api/plugin/pair")
    assert fetch.calls[0]["payload"]["deviceLabel"] == "ComfyUI plugin"

    status = remote.get_status(connection_path(tmp_path))
    assert status["state"] == "pairing"
    assert status["user_code"] == "7Q2M-4KXR"


def test_start_pairing_offline_is_graceful():
    fetch = make_fetch([remote.RemoteUnavailable("dns down")])
    result = run(remote.start_pairing(fetch=fetch))
    assert result["state"] == "offline"


def test_poll_without_pending_reports_current_state(tmp_path):
    result = run(remote.poll_pairing(connection_path(tmp_path), fetch=make_fetch([])))
    assert result["state"] == "disconnected"


def test_poll_pending_then_approved_writes_connection(tmp_path):
    path = connection_path(tmp_path)
    start = make_fetch([(200, {"deviceCode": "pld_secret", "userCode": "AAAA-BBBB"})])
    run(remote.start_pairing(fetch=start))

    poll = make_fetch(
        [
            (200, {"status": "pending"}),
            (200, {"status": "approved", "token": "plt_token", "accountEmail": "owner@example.com"}),
        ]
    )

    first = run(remote.poll_pairing(path, fetch=poll))
    assert first["state"] == "pairing"

    second = run(remote.poll_pairing(path, fetch=poll))
    assert second == {"state": "connected", "account_email": "owner@example.com"}
    assert poll.calls[0]["payload"] == {"deviceCode": "pld_secret"}

    with open(path, encoding="utf-8") as handle:
        saved = json.load(handle)
    assert saved["token"] == "plt_token"
    assert saved["account_email"] == "owner@example.com"

    status = remote.get_status(path)
    assert status["state"] == "connected"
    assert status["account_email"] == "owner@example.com"


def test_poll_denied_clears_pending(tmp_path):
    path = connection_path(tmp_path)
    run(remote.start_pairing(fetch=make_fetch([(200, {"deviceCode": "pld_secret", "userCode": "X"})])))

    result = run(remote.poll_pairing(path, fetch=make_fetch([(200, {"status": "denied"})])))

    assert result == {"state": "failed", "reason": "denied"}
    assert remote.get_status(path)["state"] == "disconnected"


def test_poll_offline_keeps_pairing_alive(tmp_path):
    path = connection_path(tmp_path)
    run(remote.start_pairing(fetch=make_fetch([(200, {"deviceCode": "pld_secret", "userCode": "X"})])))

    result = run(remote.poll_pairing(path, fetch=make_fetch([remote.RemoteUnavailable("timeout")])))

    assert result["state"] == "offline"
    assert remote.get_status(path)["state"] == "pairing"


def test_disconnect_revokes_and_clears(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(
        path,
        {"server_url": "https://trypluribus.com", "token": "plt_token", "account_email": "o@e.com"},
    )
    fetch = make_fetch([(200, {"success": True})])

    result = run(remote.disconnect(path, fetch=fetch))

    assert result["state"] == "disconnected"
    assert not os.path.exists(path)
    assert fetch.calls[0]["url"].endswith("/api/plugin/revoke")
    assert fetch.calls[0]["token"] == "plt_token"


def test_disconnect_offline_preserves_token_for_revocation_retry(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})

    result = run(remote.disconnect(path, fetch=make_fetch([remote.RemoteUnavailable("down")])))

    assert result["state"] == "offline"
    assert os.path.exists(path)
    assert remote.read_connection(path)["token"] == "plt_token"


def test_disconnect_server_error_preserves_token(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})

    result = run(remote.disconnect(path, fetch=make_fetch([(500, {"message": "down"})])))

    assert result["state"] == "error"
    assert os.path.exists(path)


def test_disconnect_unauthorized_clears_already_invalid_token(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})

    result = run(remote.disconnect(path, fetch=make_fetch([(401, {})])))

    assert result["state"] == "disconnected"
    assert not os.path.exists(path)


def test_read_connection_rejects_garbage(tmp_path):
    path = connection_path(tmp_path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("not json")
    assert remote.read_connection(path) is None


def test_push_invite_returns_none_when_disconnected(tmp_path):
    result = run(remote.push_invite(connection_path(tmp_path), {"name": "Marcus"}, fetch=make_fetch([])))
    assert result is None


def test_push_invite_sends_payload_and_returns_canonical_record(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(
        path, {"server_url": "https://trypluribus.com", "token": "plt_token"}
    )
    fetch = make_fetch(
        [
            (
                200,
                {
                    "invite": {
                        "id": "inv-1",
                        "acceptCode": "PL-AAAA-BBBB",
                        "acceptUrl": "https://trypluribus.com/accept/PL-AAAA-BBBB",
                        "emailDelivery": "sent",
                        "emailAttemptState": "sent",
                        "emailAttemptStartedAt": "2026-07-11T15:00:00Z",
                        "emailReconciliationRequired": False,
                        "status": "sent",
                    }
                },
            )
        ]
    )

    result = run(
        remote.push_invite(
            path,
            {
                "name": "Marcus Reed",
                "email": "marcus@example.com",
                "note": "hi",
                "source_key": "marcus_ref.png",
                "source_kind": "reference",
                "workflow_name": "Morning People",
                "workflow_fingerprint": "a" * 64,
                "scope_statements": ["Use of their likeness in this workflow"],
                "delivery": "email",
                "client_request_id": "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4",
            },
            fetch=fetch,
        )
    )

    assert result["state"] == "sent"
    assert result["accept_code"] == "PL-AAAA-BBBB"
    assert result["email_delivery"] == "sent"
    assert result["email_attempt_state"] == "sent"
    assert result["email_attempt_started_at"] == "2026-07-11T15:00:00Z"
    assert result["email_reconciliation_required"] is False
    call = fetch.calls[0]
    assert call["url"].endswith("/api/plugin/invites")
    assert call["token"] == "plt_token"
    assert call["payload"]["name"] == "Marcus Reed"
    assert "workflowName" not in call["payload"]
    assert "workflowFingerprint" not in call["payload"]
    assert "sourceKey" not in call["payload"]
    assert "sourceKind" not in call["payload"]
    assert call["payload"]["scopeStatements"] == ["Use of their likeness in this workflow"]
    assert call["payload"]["clientRequestId"] == "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4"


@pytest.mark.parametrize("email", ["", "not-an-email", "missing@domain"])
def test_push_invite_rejects_invalid_email_without_network_call(tmp_path, email):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([])

    result = run(
        remote.push_invite(
            path, {"name": "Marcus", "email": email, "delivery": "email"}, fetch=fetch
        )
    )

    assert result["state"] == "validation_error"
    assert fetch.calls == []


def test_push_link_invite_omits_blank_optional_fields(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(
        path,
        {
            "token": "plt_token",
            "server_url": "https://trypluribus.com",
        },
    )
    fetch = make_fetch(
        [
            (
                200,
                {
                    "invite": {
                        "id": "inv-1",
                        "acceptCode": "PL-AAAA-BBBB-CCCC-DDDD",
                        "acceptUrl": "https://trypluribus.com/accept/PL-AAAA-BBBB-CCCC-DDDD",
                        "emailDelivery": "skipped",
                        "emailAttemptState": "not_attempted",
                        "status": "sent",
                    }
                },
            )
        ]
    )
    request_id = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4"

    result = run(
        remote.push_invite(
            path,
            {
                "name": "Legacy Link Talent",
                "email": "",
                "note": "",
                "source_key": "",
                "source_kind": "",
                "workflow_name": "",
                "workflow_fingerprint": "",
                "scope_statements": [],
                "delivery": "link",
                "client_request_id": request_id,
            },
            fetch=fetch,
        )
    )

    assert result["state"] == "sent"
    assert fetch.calls[0]["payload"] == {
        "name": "Legacy Link Talent",
        "delivery": "link",
        "clientRequestId": request_id,
    }


def test_push_invite_copy_link_allows_blank_email(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch(
        [
            (
                200,
                {
                    "invite": {
                        "acceptCode": "PL-CCCC-DDDD",
                        "emailDelivery": "skipped",
                        "status": "sent",
                    }
                },
            )
        ]
    )

    result = run(
        remote.push_invite(
            path, {"name": "Marcus", "email": "", "delivery": "link"}, fetch=fetch
        )
    )

    assert result["state"] == "sent"
    assert fetch.calls[0]["payload"]["delivery"] == "link"


def test_push_invite_maps_transport_ambiguity_and_unauthorized(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})

    unconfirmed = run(
        remote.push_invite(
            path,
            {"name": "M", "delivery": "link"},
            fetch=make_fetch([remote.RemoteUnavailable("down")]),
        )
    )
    assert unconfirmed == {"state": "unconfirmed"}

    unauthorized = run(
        remote.push_invite(
            path, {"name": "M", "delivery": "link"}, fetch=make_fetch([(401, {})])
        )
    )
    assert unauthorized == {"state": "unauthorized"}


def test_push_invite_500_after_possible_commit_is_unconfirmed(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})

    result = run(
        remote.push_invite(
            path,
            {"name": "Marcus", "delivery": "link"},
            fetch=make_fetch([(500, {"message": "audit failed"})]),
        )
    )

    assert result == {"state": "unconfirmed", "message": "audit failed"}


def test_push_invite_malformed_200_is_unconfirmed(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})

    result = run(
        remote.push_invite(
            path,
            {"name": "Marcus", "delivery": "link"},
            fetch=make_fetch([(200, {"invite": {"status": "sent"}})]),
        )
    )

    assert result["state"] == "unconfirmed"


def test_push_invite_422_is_definite_error(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})

    result = run(
        remote.push_invite(
            path,
            {"name": "Marcus", "delivery": "link"},
            fetch=make_fetch([(422, {"message": "invalid scope"})]),
        )
    )

    assert result == {"state": "error", "message": "invalid scope"}


def test_fetch_invite_statuses(tmp_path):
    path = connection_path(tmp_path)
    assert run(remote.fetch_invite_statuses(path, fetch=make_fetch([]))) == {
        "state": "disconnected",
        "invites": [],
    }

    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch(
        [
            (
                200,
                {
                    "invites": [
                        {
                            "acceptCode": "PL-AAAA-BBBB",
                            "status": "accepted",
                            "emailAttemptState": "manual_reconciliation",
                            "emailAttemptStartedAt": "2026-07-10T12:00:00Z",
                            "emailReconciliationRequired": True,
                        }
                    ]
                },
            )
        ]
    )
    result = run(remote.fetch_invite_statuses(path, fetch=fetch))
    assert result["state"] == "ok"
    assert result["invites"][0]["status"] == "accepted"
    assert result["invites"][0]["emailAttemptState"] == "manual_reconciliation"
    assert result["invites"][0]["emailAttemptStartedAt"] == "2026-07-10T12:00:00Z"
    assert result["invites"][0]["emailReconciliationRequired"] is True
    assert fetch.calls[0]["method"] == "GET"


def test_workspace_proxy_requires_connection_and_maps_offline(tmp_path):
    path = connection_path(tmp_path)
    status, result = run(remote.fetch_workspace(path, fetch=make_fetch([])))
    assert status == 401
    assert result["state"] == "disconnected"

    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    status, result = run(
        remote.fetch_workspace(path, fetch=make_fetch([remote.RemoteUnavailable("down")]))
    )
    assert status == 503
    assert result["state"] == "offline"


def test_workspace_project_and_person_proxies_send_only_contract_fields(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([(200, {"ok": True}), (200, {"ok": True}), (200, {"ok": True})])

    run(
        remote.create_workspace(
            path,
            {"organizationName": "Studio", "licenseeType": "individual", "secret": "drop"},
            fetch,
        )
    )
    run(
        remote.create_project(
            path,
            {
                "title": "Campaign",
                "clientName": "Client",
                "description": "Ad",
                "workflowName": "must not pass",
            },
            fetch,
        )
    )
    run(
        remote.create_project_person(
            path,
            "project-1",
            {
                "mode": "new",
                "displayName": "Alex Person",
                "talentEmail": "alex@example.com",
                "sourceKey": "/private/alex.png",
                "representative": {
                    "role": "manager",
                    "email": "rep@example.com",
                    "nodeId": "44",
                },
            },
            fetch,
        )
    )

    assert fetch.calls[0]["payload"] == {
        "organizationName": "Studio",
        "licenseeType": "individual",
    }
    assert fetch.calls[1]["payload"] == {
        "title": "Campaign",
        "clientName": "Client",
        "description": "Ad",
    }
    assert fetch.calls[2]["payload"] == {
        "mode": "new",
        "displayName": "Alex Person",
        "talentEmail": "alex@example.com",
        "representative": {"role": "manager", "email": "rep@example.com"},
    }


def test_project_person_proxy_forwards_stable_client_person_id(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([(201, {"person": {"id": "person-1"}})])
    client_person_id = "11111111-1111-4111-8111-111111111111"

    status, _ = run(
        remote.create_project_person(
            path,
            "project-1",
            {
                "mode": "new",
                "displayName": "Alex Person",
                "clientPersonId": client_person_id,
            },
            fetch,
        )
    )

    assert status == 201
    assert fetch.calls[0]["payload"]["clientPersonId"] == client_person_id



def test_project_person_update_is_patch_scoped_and_cannot_write_talent_profile(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([(200, {"person": {"id": "person-1"}})])

    status, _ = run(
        remote.update_project_person(
            path,
            "project-1",
            "person-1",
            {
                "displayName": "Alex Person",
                "role": "Lead",
                "talentEmail": "must-not-pass@example.com",
                "sourceKey": "/private/alex.png",
                "representative": {
                    "role": "manager",
                    "name": "Riley Manager",
                    "email": "riley@example.com",
                    "nodeId": "44",
                },
            },
            fetch,
        )
    )

    assert status == 200
    assert fetch.calls[0] == {
        "method": "PATCH",
        "url": "https://x/api/plugin/projects/project-1/people/person-1",
        "payload": {
            "displayName": "Alex Person",
            "role": "Lead",
            "representative": {
                "role": "manager",
                "name": "Riley Manager",
                "email": "riley@example.com",
            },
        },
        "token": "plt_token",
    }


def test_project_sync_can_be_scoped_to_active_workflow(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([(200, {"projectContext": {}})])
    workflow_ref = str(uuid.uuid4())

    status, _ = run(
        remote.fetch_project(path, "project-1", workflow_ref, fetch)
    )

    assert status == 200
    assert fetch.calls[0]["url"] == (
        f"https://x/api/plugin/projects/project-1?workflowRef={workflow_ref}"
    )


def test_source_links_proxy_strips_paths_prompts_and_node_ids(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([(200, {"project": {}})])
    workflow_ref = str(uuid.uuid4())
    body = {
        "workflowRef": workflow_ref,
        "workflowKind": "storyboard",
        "baseManifestVersion": 3,
        "graphHash": "a" * 64,
        "identityReviewHash": "c" * 64,
        "identityRevision": 7,
        "sources": [
            {
                "sourceRef": "b" * 64,
                "sourceKind": "reference",
                "sourceKey": "/Users/alex/private-face.png",
                "sourceNodeId": "12",
                "prompt": "put Alex in an ad",
                "disposition": "linked",
                "talentRecordIds": ["talent-1"],
                "operations": [
                    {"class_type": "IPAdapter", "node_id": "99"},
                ],
            }
        ],
    }

    status, _ = run(remote.put_project_source_links(path, "project-1", body, fetch))

    assert status == 200
    payload = fetch.calls[0]["payload"]
    assert payload["workflowRef"] == workflow_ref
    assert payload["baseManifestVersion"] == 3
    assert payload["graphHash"] == "a" * 64
    assert payload["identityReviewHash"] == "c" * 64
    assert payload["identityRevision"] == 7
    assert payload["sources"][0]["operations"] == [{"classType": "IPAdapter"}]
    serialized = json.dumps(payload)
    assert "private-face" not in serialized
    assert "put Alex" not in serialized
    assert "node_id" not in serialized
    assert "sourceNodeId" not in serialized


def test_source_links_proxy_requires_manifest_base_version(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([])
    body = {
        "workflowRef": str(uuid.uuid4()),
        "workflowKind": "storyboard",
        "sources": [],
    }

    with pytest.raises(ValueError, match="baseManifestVersion"):
        run(remote.put_project_source_links(path, "project-1", body, fetch))
    assert fetch.calls == []


def test_use_proxy_rejects_local_graph_material_without_network_call(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([])

    with pytest.raises(ValueError, match="local-only"):
        run(
            remote.put_project_use(
                path,
                "project-1",
                {"brandName": "Client", "prompt": "private generation prompt"},
                fetch,
            )
        )
    assert fetch.calls == []


def test_use_proxy_rebuilds_structured_scope_and_people_payload(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([(200, {"scope": {}})])
    workflow_ref = str(uuid.uuid4())

    status, _ = run(
        remote.put_project_use(
            path,
            "project-1",
            {
                "workflowRef": workflow_ref,
                "rightsManifestHash": "a" * 64,
                "usageType": "AI-assisted advertising video",
                "deliverables": ["15-second video"],
                "channels": ["social"],
                "platforms": ["Instagram"],
                "territories": ["United States"],
                "languages": ["English"],
                "usageWindowStart": "2026-08-01",
                "usageWindowEnd": None,
                "productCategory": "Footwear",
                "paidMediaAllowed": True,
                "organicMediaAllowed": True,
                "compensationHandling": "handled_separately",
                "compensation": None,
                "exclusivityHandling": "not_part_of_request",
                "exclusivity": None,
                "finalCreativeApprovalRequired": True,
                "aiActions": [
                    {
                        "talentRecordId": "talent-1",
                        "modality": "face",
                        "action": "edit",
                        "requiresFinalApproval": True,
                        "notes": "Identity-preserving image edit",
                        "untrustedExtra": "/private/source.png",
                    }
                ],
                "people": [
                    {
                        "talentRecordId": "talent-1",
                        "restrictions": None,
                        "usageComfort": "Paid social only",
                        "representativeAuthority": None,
                        "untrustedExtra": "/private/source.png",
                    }
                ],
                "untrustedExtra": {"raw": "graph"},
            },
            fetch,
        )
    )

    assert status == 200
    assert fetch.calls[0]["payload"] == {
        "workflowRef": workflow_ref,
        "usageType": "AI-assisted advertising video",
        "deliverables": ["15-second video"],
        "channels": ["social"],
        "platforms": ["Instagram"],
        "territories": ["United States"],
        "languages": ["English"],
        "paidMediaAllowed": True,
        "organicMediaAllowed": True,
        "usageWindowStart": "2026-08-01",
        "usageWindowEnd": None,
        "productCategory": "Footwear",
        "finalCreativeApprovalRequired": True,
        "compensationHandling": "handled_separately",
        "compensation": None,
        "exclusivityHandling": "not_part_of_request",
        "exclusivity": None,
        "rightsManifestHash": "a" * 64,
        "aiActions": [
            {
                "talentRecordId": "talent-1",
                "modality": "face",
                "action": "edit",
                "requiresFinalApproval": True,
                "notes": "Identity-preserving image edit",
            }
        ],
        "people": [
            {
                "talentRecordId": "talent-1",
                "restrictions": None,
                "usageComfort": "Paid social only",
                "representativeAuthority": None,
            }
        ],
    }


def test_confirmation_proxy_preserves_stable_request_and_workflow_refs(tmp_path):
    path = connection_path(tmp_path)
    remote.write_connection(path, {"server_url": "https://x", "token": "plt_token"})
    fetch = make_fetch([(201, {"confirmationRequest": {"id": "request-1"}})])
    request_id = str(uuid.uuid4())
    workflow_ref = str(uuid.uuid4())

    status, _ = run(
        remote.create_confirmation_request(
            path,
            "project-1",
            {
                "clientRequestId": request_id,
                "workflowRef": workflow_ref,
                "rightsManifestHash": "a" * 64,
                "talentRecordId": "talent-1",
                "recipientEmail": "rep@example.com",
                "delivery": "email",
                "sourcePath": "/private/ref.png",
            },
            fetch,
        )
    )

    assert status == 201
    assert fetch.calls[0]["payload"] == {
        "clientRequestId": request_id,
        "workflowRef": workflow_ref,
        "rightsManifestHash": "a" * 64,
        "talentRecordId": "talent-1",
        "recipientEmail": "rep@example.com",
        "delivery": "email",
    }
