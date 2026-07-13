import asyncio
import json
import os

import pytest

from pluribus import remote
from pluribus.invites import (
    client_request_id_for_invite,
    read_actions,
    record_action,
)
from pluribus.server import register_routes

SEED = os.path.join(os.path.dirname(__file__), "..", "seed", "roster.json")


class FakeRoutes:
    def __init__(self):
        self.handlers = {}

    def _register(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler

        return decorator

    def post(self, path):
        return self._register("POST", path)

    def get(self, path):
        return self._register("GET", path)


class FakePromptServer:
    def __init__(self):
        self.routes = FakeRoutes()


class FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_action_route_preserves_explicit_client_request_id(tmp_path, monkeypatch):
    prompt_server = FakePromptServer()
    actions_path = str(tmp_path / "invites.json")
    seen = {}

    async def push_invite(_connection_path, invite):
        seen.update(invite)
        return {"state": "unconfirmed"}

    monkeypatch.setattr(remote, "push_invite", push_invite)
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=actions_path,
        connection_path=str(tmp_path / "connection.json"),
    )
    request_id = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4"
    handler = prompt_server.routes.handlers[("POST", "/pluribus/action")]
    response = run(
        handler(
            FakeRequest(
                {
                    "kind": "invite",
                    "name": "Marcus Reed",
                    "source_key": "marcus_ref.png",
                    "source_kind": "reference",
                    "workflow_name": "Morning People",
                    "workflow_fingerprint": "a" * 64,
                    "scope_statements": ["Use of their likeness"],
                    "delivery": "link",
                    "client_request_id": request_id,
                }
            )
        )
    )

    payload = response_json(response)
    assert response.status == 200
    assert seen["client_request_id"] == request_id
    assert payload["action"]["client_request_id"] == request_id


def test_action_route_reuses_matching_unconfirmed_draft_before_new_browser_id(
    tmp_path, monkeypatch
):
    prompt_server = FakePromptServer()
    actions_path = str(tmp_path / "invites.json")
    invite = {
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": "Morning People",
        "workflow_fingerprint": "a" * 64,
        "scope_statements": ["Use of their likeness"],
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
    }
    persisted = record_action(
        actions_path,
        "invite",
        **invite,
        draft_reason="unconfirmed",
    )
    seen = {}

    async def push_invite(_connection_path, body):
        seen.update(body)
        return {"state": "unconfirmed"}

    monkeypatch.setattr(remote, "push_invite", push_invite)
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=actions_path,
        connection_path=str(tmp_path / "connection.json"),
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/action")]
    response = run(
        handler(
            FakeRequest(
                {
                    "kind": "invite",
                    **invite,
                    "client_request_id": "5bde7ce8-a4ef-4ec5-a627-042e8f66d496",
                }
            )
        )
    )

    payload = response_json(response)
    assert response.status == 200
    assert seen["client_request_id"] == persisted["client_request_id"]
    assert payload["action"]["client_request_id"] == persisted["client_request_id"]
    assert len(read_actions(actions_path)) == 1


def test_action_route_journals_frozen_request_before_remote_io(tmp_path, monkeypatch):
    prompt_server = FakePromptServer()
    actions_path = str(tmp_path / "invites.json")
    connection_path = str(tmp_path / "connection.json")
    remote.write_connection(connection_path, {"token": "plt_test"})
    body = {
        "kind": "invite",
        "talent_id": "t_marcus",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": "Morning People",
        "workflow_fingerprint": "a" * 64,
        "scope_statements": ["Use of their likeness"],
        "email": "marcus@example.com",
        "note": "Please review",
        "delivery": "email",
        "client_request_id": "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4",
    }
    seen = {}

    async def push_invite(_connection_path, invite):
        records = read_actions(actions_path)
        assert len(records) == 1
        assert records[0]["draft_reason"] == "in_flight"
        assert records[0]["client_request_id"] == invite["client_request_id"]
        assert records[0]["workflow_fingerprint"] == "a" * 64
        seen.update(invite)
        return {"state": "unconfirmed"}

    monkeypatch.setattr(remote, "push_invite", push_invite)
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=actions_path,
        connection_path=connection_path,
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/action")]

    response = run(handler(FakeRequest(body)))

    assert response.status == 200
    assert seen["client_request_id"] == body["client_request_id"]
    assert read_actions(actions_path)[0]["draft_reason"] == "unconfirmed"


def test_action_route_crash_after_remote_commit_keeps_preflight_journal(
    tmp_path, monkeypatch
):
    prompt_server = FakePromptServer()
    actions_path = str(tmp_path / "invites.json")
    connection_path = str(tmp_path / "connection.json")
    remote.write_connection(connection_path, {"token": "plt_test"})
    request_id = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4"

    async def push_invite(_connection_path, _invite):
        raise asyncio.CancelledError()

    monkeypatch.setattr(remote, "push_invite", push_invite)
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=actions_path,
        connection_path=connection_path,
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/action")]
    body = {
        "kind": "invite",
        "name": "Marcus Reed",
        "source_key": "marcus_ref.png",
        "source_kind": "reference",
        "workflow_name": "Morning People",
        "workflow_fingerprint": "a" * 64,
        "scope_statements": ["Use of their likeness"],
        "delivery": "link",
        "client_request_id": request_id,
    }

    with pytest.raises(asyncio.CancelledError):
        run(handler(FakeRequest(body)))

    persisted = read_actions(actions_path)
    assert len(persisted) == 1
    assert persisted[0]["client_request_id"] == request_id
    assert persisted[0]["draft_reason"] == "in_flight"
    assert client_request_id_for_invite(
        actions_path,
        {
            **body,
            "client_request_id": "5bde7ce8-a4ef-4ec5-a627-042e8f66d496",
        },
    ) == request_id


def test_action_route_fails_closed_when_preflight_journal_cannot_write(
    tmp_path, monkeypatch
):
    prompt_server = FakePromptServer()
    pushed = False
    connection_path = str(tmp_path / "connection.json")
    remote.write_connection(connection_path, {"token": "plt_test"})

    def fail_record(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    async def push_invite(_connection_path, _invite):
        nonlocal pushed
        pushed = True
        return {"state": "sent"}

    monkeypatch.setattr("pluribus.server.record_action", fail_record)
    monkeypatch.setattr(remote, "push_invite", push_invite)
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=connection_path,
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/action")]
    response = run(
        handler(
            FakeRequest(
                {
                    "kind": "invite",
                    "name": "Marcus Reed",
                    "source_key": "marcus_ref.png",
                    "delivery": "link",
                }
            )
        )
    )

    assert response.status == 500
    assert pushed is False
    assert "nothing was sent" in response_json(response)["message"]


def test_scan_routes_register_and_wrapped_scan_returns_context(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=str(tmp_path / "connection.json"),
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/scan")]
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "marcus_ref.png"}},
        "2": {"class_type": "ReActorFaceSwap", "inputs": {"source_image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }
    response = run(
        handler(
            FakeRequest(
                {
                    "workflow": workflow,
                    "workflow_name": "Morning People",
                    "workflow_fingerprint": "b" * 64,
                }
            )
        )
    )
    payload = response_json(response)

    assert response.status == 200
    assert payload["workflow_name"] == "Morning People"
    assert payload["workflow_fingerprint"] == "b" * 64
    assert payload["persons"][0]["workflow_fingerprint"] == "b" * 64
