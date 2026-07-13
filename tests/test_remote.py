import asyncio
import json
import os

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
    assert "ComfyUI" in fetch.calls[0]["payload"]["deviceLabel"]

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
    assert call["payload"]["workflowName"] == "Morning People"
    assert call["payload"]["workflowFingerprint"] == "a" * 64
    assert call["payload"]["sourceKind"] == "reference"
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
