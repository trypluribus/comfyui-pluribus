import asyncio
import hashlib
import inspect
import json
import os

import pytest

from pluribus import remote
from pluribus.bindings import BindingStore
from pluribus.invites import (
    client_request_id_for_invite,
    read_actions,
    record_action,
)
from pluribus.server import register_routes
from pluribus.identity_analyzers import AnalyzerStatus
from pluribus.identity_service import (
    IdentityAnalysisService,
    IdentityCapacityError,
    IdentityConflictError,
    IdentityPersistenceError,
)
from pluribus.storage import write_private_json

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

    def put(self, path):
        return self._register("PUT", path)

    def patch(self, path):
        return self._register("PATCH", path)

    def delete(self, path):
        return self._register("DELETE", path)


class FakePromptServer:
    def __init__(self):
        self.routes = FakeRoutes()


class FakeRequest:
    def __init__(
        self,
        body,
        match_info=None,
        query=None,
        *,
        headers=None,
        content_type="application/json",
        host="127.0.0.1:8188",
    ):
        self.body = body
        self.match_info = match_info or {}
        self.query = query or {}
        self.headers = headers or {}
        self.content_type = content_type
        self.host = host

    async def json(self):
        return self.body


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_identity_decision_and_reconciliation_routes_use_local_coordinator(tmp_path):
    class FakeDecisions:
        def __init__(self):
            self.calls = []

        def put_decision(self, job_id, body):
            self.calls.append((job_id, body))
            return {
                "jobId": job_id,
                "revision": 4,
                "links": [],
                "personDrafts": [],
                "syncState": "saved_local",
                "syncDetails": {
                    "state": "saved_local",
                    "entryId": "entry-1",
                    "workflowRef": "workflow-1",
                },
            }

        async def drain_sync_entry(self, _entry_id):
            raise OSError("offline")

        def reconciliation_preview(self, job_id):
            return {"jobId": job_id, "readOnly": True}

        async def drain_pending_async(self):
            return []

        def sync_status(self):
            return []

        def mark_workflow_revision_synced(self, *_args):
            return []

    decisions = FakeDecisions()
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        identity_service=object(),
        identity_decision_service=decisions,
    )
    decision = prompt_server.routes.handlers[
        ("PUT", "/pluribus/identity/jobs/{job_id}/decision")
    ]
    preview = prompt_server.routes.handlers[
        ("GET", "/pluribus/identity/jobs/{job_id}/reconciliation")
    ]

    response = run(
        decision(
            FakeRequest(
                {"baseRevision": 3, "candidateId": "candidate-a"},
                {"job_id": "job-1"},
            )
        )
    )
    assert response_json(response)["syncState"] == "saved_local"
    assert decisions.calls == [
        ("job-1", {"baseRevision": 3, "candidateId": "candidate-a"})
    ]
    assert response_json(
        run(preview(FakeRequest(None, {"job_id": "job-1"})))
    ) == {"jobId": "job-1", "readOnly": True}


def test_identity_persistence_failure_maps_to_http_503(tmp_path):
    class BrokenDecisions:
        def put_decision(self, _job_id, _body):
            raise IdentityPersistenceError("Private identity journal is corrupt.")

    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        identity_service=object(),
        identity_decision_service=BrokenDecisions(),
    )
    handler = prompt_server.routes.handlers[
        ("PUT", "/pluribus/identity/jobs/{job_id}/decision")
    ]

    response = run(
        handler(FakeRequest({}, {"job_id": "job-1"}))
    )

    assert response.status == 503
    assert "corrupt" in response_json(response)["message"]


def test_post_commit_malformed_sync_response_does_not_turn_local_save_into_error(
    tmp_path,
):
    class MalformedDrainDecisions:
        def put_decision(self, job_id, _body):
            return {
                "jobId": job_id,
                "revision": 1,
                "links": [],
                "personDrafts": [],
                "syncState": "sync_pending",
                "syncDetails": {
                    "state": "sync_pending",
                    "entryId": "entry-1",
                    "workflowRef": "workflow-1",
                },
            }

        async def drain_sync_entry(self, _entry_id):
            raise IdentityPersistenceError("Malformed successful workspace response.")

        async def drain_pending_async(self):
            return []

        def sync_status(self):
            return []

    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        identity_service=object(),
        identity_decision_service=MalformedDrainDecisions(),
    )
    handler = prompt_server.routes.handlers[
        ("PUT", "/pluribus/identity/jobs/{job_id}/decision")
    ]

    response = run(handler(FakeRequest({}, {"job_id": "job-1"})))

    assert response.status == 200
    assert response_json(response)["revision"] == 1
    assert response_json(response)["syncState"] == "sync_pending"


def test_protected_routes_reject_cross_site_requests_and_non_json_bodies(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=str(tmp_path / "actions.json"),
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/scan")]

    cross_site = run(
        handler(
            FakeRequest(
                {"workflow": {}},
                headers={
                    "Origin": "https://attacker.example",
                    "Sec-Fetch-Site": "cross-site",
                },
                content_type="text/plain",
            )
        )
    )
    assert cross_site.status == 403
    assert response_json(cross_site)["state"] == "forbidden"

    wrong_type = run(
        handler(FakeRequest({"workflow": {}}, content_type="text/plain"))
    )
    assert wrong_type.status == 400
    assert "application/json" in response_json(wrong_type)["message"]

    same_origin = run(
        handler(
            FakeRequest(
                {"workflow": {}},
                headers={"Origin": "http://127.0.0.1:8188"},
            )
        )
    )
    assert same_origin.status == 200


def test_every_state_changing_route_uses_the_same_origin_guard():
    source = inspect.getsource(register_routes)
    assert "@routes.post(" not in source
    assert "@routes.put(" not in source
    assert "@routes.patch(" not in source
    assert "@routes.delete(" not in source
    assert '@_mutation_route(routes.get, "/pluribus/connect")' in source
    assert '@_mutation_route(routes.get, "/pluribus/identity/sync")' in source


def test_identity_json_responses_are_never_cached(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=str(tmp_path / "actions.json"),
    )
    handler = prompt_server.routes.handlers[
        ("GET", "/pluribus/identity/capabilities")
    ]

    response = run(handler(FakeRequest(None)))

    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Vary"] == "Origin"


def test_identity_queue_capacity_maps_to_http_429(tmp_path):
    class BusyIdentityService:
        async def start_job(self, _body):
            raise IdentityCapacityError("Too many local identity analyses are queued.")

    bindings_path = str(tmp_path / "bindings.json")
    bindings = BindingStore(bindings_path)
    workflow = bindings.resolve_workflow("busy-workflow")
    source = bindings.resolve_source(
        workflow["workflowRef"], "portrait.jpg", "reference"
    )
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=str(tmp_path / "actions.json"),
        bindings_path=bindings_path,
        identity_service=BusyIdentityService(),
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/identity/analyze")]

    response = run(
        handler(
            FakeRequest(
                {
                    "workflowRef": workflow["workflowRef"],
                    "sources": [
                        {
                            "sourceRef": source["sourceRef"],
                            "sourceKey": "portrait.jpg",
                            "sourceKind": "reference",
                        }
                    ],
                }
            )
        )
    )

    assert response.status == 429
    assert response.headers["Cache-Control"] == "no-store"


def test_identity_link_revision_conflict_maps_to_http_409(tmp_path):
    class ConflictingIdentityService:
        def put_links(self, _job_id, _body):
            raise IdentityConflictError(
                "Identity link revision conflict. Reload before saving."
            )

    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=str(tmp_path / "actions.json"),
        identity_service=ConflictingIdentityService(),
    )
    handler = prompt_server.routes.handlers[
        ("PUT", "/pluribus/identity/jobs/{job_id}/links")
    ]

    response = run(
        handler(
            FakeRequest(
                {"baseRevision": 1, "links": []},
                match_info={"job_id": "identity-job"},
            )
        )
    )

    assert response.status == 409
    assert "reload" in response_json(response)["message"].lower()
    assert response.headers["Cache-Control"] == "no-store"


def test_identity_link_delete_revision_conflict_maps_to_http_409(tmp_path):
    class ConflictingIdentityService:
        def delete_links(self, _job_id, _body):
            raise IdentityConflictError(
                "Identity link revision conflict. Reload before clearing."
            )

    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=SEED,
        actions_path=str(tmp_path / "actions.json"),
        identity_service=ConflictingIdentityService(),
    )
    handler = prompt_server.routes.handlers[
        ("DELETE", "/pluribus/identity/jobs/{job_id}/links")
    ]

    response = run(
        handler(
            FakeRequest(
                {"baseRevision": 1},
                match_info={"job_id": "identity-job"},
            )
        )
    )

    assert response.status == 409
    assert "reload" in response_json(response)["message"].lower()
    assert response.headers["Cache-Control"] == "no-store"


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


def test_clean_runtime_scan_has_no_bundled_roster_clearance(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=str(tmp_path / "connection.json"),
    )
    handler = prompt_server.routes.handlers[("POST", "/pluribus/scan")]
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "sarah_ref.png"}},
        "2": {"class_type": "IPAdapter", "inputs": {"image": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }

    response = run(handler(FakeRequest({"workflow": workflow, "workflow_name": "Fresh"})))
    person = response_json(response)["persons"][0]

    assert person["state"] == "unidentified"
    assert person["talent_id"] is None
    assert person["available_actions"] == ["link", "not_person", "review"]


def test_binding_routes_mint_opaque_refs_and_associate_project(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=str(tmp_path / "connection.json"),
        bindings_path=str(tmp_path / "bindings.json"),
    )
    resolve = prompt_server.routes.handlers[("POST", "/pluribus/workflows/resolve")]
    resolved = response_json(
        run(
            resolve(
                FakeRequest(
                    {"localWorkflowKey": "/private/Client Ad.json", "graphHash": "a" * 64}
                )
            )
        )
    )
    workflow_ref = resolved["workflowRef"]

    associate = prompt_server.routes.handlers[("PUT", "/pluribus/workflows/{workflow_ref}")]
    associated = response_json(
        run(
            associate(
                FakeRequest(
                    {"projectId": "project-1", "workflowKind": "storyboard"},
                    {"workflow_ref": workflow_ref},
                )
            )
        )
    )
    source_resolve = prompt_server.routes.handlers[
        ("POST", "/pluribus/workflows/{workflow_ref}/sources/resolve")
    ]
    source = response_json(
        run(
            source_resolve(
                FakeRequest(
                    {
                        "localSourceKey": "/private/people/alex-reference.png",
                        "sourceKind": "reference",
                    },
                    {"workflow_ref": workflow_ref},
                )
            )
        )
    )

    assert associated["projectId"] == "project-1"
    assert associated["workflowKind"] == "storyboard"
    assert len(source["sourceRef"]) == 64
    assert "alex-reference" not in json.dumps(source)


def test_local_person_draft_routes_are_private_and_support_source_filtering(tmp_path):
    prompt_server = FakePromptServer()
    bindings_path = str(tmp_path / "bindings.json")
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=str(tmp_path / "connection.json"),
        bindings_path=bindings_path,
    )
    resolve = prompt_server.routes.handlers[("POST", "/pluribus/workflows/resolve")]
    workflow_ref = response_json(
        run(resolve(FakeRequest({"localWorkflowKey": "private-workflow"})))
    )["workflowRef"]
    source_resolve = prompt_server.routes.handlers[
        ("POST", "/pluribus/workflows/{workflow_ref}/sources/resolve")
    ]
    source_a = response_json(
        run(
            source_resolve(
                FakeRequest(
                    {"localSourceKey": "/private/alex-a.png", "sourceKind": "reference"},
                    {"workflow_ref": workflow_ref},
                )
            )
        )
    )["sourceRef"]
    source_b = response_json(
        run(
            source_resolve(
                FakeRequest(
                    {"localSourceKey": "/private/alex-b.png", "sourceKind": "reference"},
                    {"workflow_ref": workflow_ref},
                )
            )
        )
    )["sourceRef"]
    put = prompt_server.routes.handlers[
        ("PUT", "/pluribus/workflows/{workflow_ref}/person-drafts")
    ]
    get = prompt_server.routes.handlers[
        ("GET", "/pluribus/workflows/{workflow_ref}/person-drafts")
    ]
    delete = prompt_server.routes.handlers[
        (
            "DELETE",
            "/pluribus/workflows/{workflow_ref}/person-drafts/{draft_id}",
        )
    ]

    saved_response = run(
        put(
            FakeRequest(
                {
                    "canonicalPersonId": "person_123",
                    "displayName": "Alex Person",
                    "role": "Actor",
                    "talentEmail": "alex@example.com",
                    "representative": {"role": "agent", "name": "Avery Agent"},
                    "notes": "Local note",
                    "sourceRefs": [source_a, source_b],
                    "workflow": {"private": True},
                    "imagePath": "/private/alex.png",
                },
                {"workflow_ref": workflow_ref},
            )
        )
    )
    saved = response_json(saved_response)["draft"]
    filtered_response = run(
        get(
            FakeRequest(
                None,
                {"workflow_ref": workflow_ref},
                {"sourceRef": source_b},
            )
        )
    )

    assert saved_response.status == 200
    assert set(saved) == {
        "draftId",
        "canonicalPersonId",
        "displayName",
        "role",
        "talentEmail",
        "representative",
        "notes",
        "sourceRefs",
    }
    assert response_json(filtered_response) == {"drafts": [saved]}
    private_text = (tmp_path / "bindings.json").read_text(encoding="utf-8")
    assert "/private" not in private_text
    assert '"workflow"' not in private_text
    assert "imagePath" not in private_text

    deleted_response = run(
        delete(
            FakeRequest(
                None,
                {"workflow_ref": workflow_ref, "draft_id": saved["draftId"]},
            )
        )
    )
    assert response_json(deleted_response) == {
        "deleted": True,
        "draftId": saved["draftId"],
    }
    assert response_json(
        run(get(FakeRequest(None, {"workflow_ref": workflow_ref})))
    ) == {"drafts": []}


@pytest.mark.parametrize("linked_identifier", ["draftId", "canonicalPersonId"])
def test_person_draft_delete_rejects_persisted_identity_link_after_restart(
    tmp_path, linked_identifier
):
    bindings_path = str(tmp_path / "bindings.json")
    bindings = BindingStore(bindings_path)
    workflow = bindings.resolve_workflow("restart-safe-person-delete")
    source = bindings.resolve_source(
        workflow["workflowRef"], "portrait.png", "reference"
    )
    draft = bindings.put_person_draft(
        workflow["workflowRef"],
        {
            "canonicalPersonId": "person_123",
            "displayName": "Alex Person",
            "sourceRefs": [source["sourceRef"]],
        },
    )

    state_dir = tmp_path / "state"
    before_restart = IdentityAnalysisService(str(state_dir), analyzer=object())
    workflow_key = hashlib.sha256(
        f"identity-links:{workflow['workflowRef']}".encode("utf-8")
    ).hexdigest()
    write_private_json(
        os.path.join(before_restart.links_dir, f"{workflow_key}.json"),
        {
            "schemaVersion": 3,
            "analysisJobId": "persisted-job",
            "revision": 1,
            "links": [
                {
                    "candidateId": "candidate-1",
                    "personId": draft[linked_identifier],
                    "state": "confirmed",
                    "occurrenceIds": ["occurrence-1"],
                }
            ],
        },
    )

    restarted_identity = IdentityAnalysisService(str(state_dir), analyzer=object())
    assert restarted_identity._jobs == {}
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        bindings_path=bindings_path,
        identity_service=restarted_identity,
    )
    delete = prompt_server.routes.handlers[
        (
            "DELETE",
            "/pluribus/workflows/{workflow_ref}/person-drafts/{draft_id}",
        )
    ]

    response = run(
        delete(
            FakeRequest(
                None,
                {
                    "workflow_ref": workflow["workflowRef"],
                    "draft_id": draft["draftId"],
                },
            )
        )
    )

    assert response.status == 409
    assert response.headers["Cache-Control"] == "no-store"
    assert "visual identity assignments" in response_json(response)["message"].lower()
    assert bindings.list_person_drafts(workflow["workflowRef"]) == [draft]


def test_local_person_draft_route_rejects_unminted_source(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        bindings_path=str(tmp_path / "bindings.json"),
    )
    resolve = prompt_server.routes.handlers[("POST", "/pluribus/workflows/resolve")]
    workflow_ref = response_json(
        run(resolve(FakeRequest({"localWorkflowKey": "private-workflow"})))
    )["workflowRef"]
    put = prompt_server.routes.handlers[
        ("PUT", "/pluribus/workflows/{workflow_ref}/person-drafts")
    ]

    response = run(
        put(
            FakeRequest(
                {"displayName": "Alex", "sourceRefs": ["a" * 64]},
                {"workflow_ref": workflow_ref},
            )
        )
    )

    assert response.status == 400
    assert "not minted" in response_json(response)["message"]


def test_local_source_review_routes_persist_without_a_pluribus_connection(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        bindings_path=str(tmp_path / "bindings.json"),
    )
    resolve = prompt_server.routes.handlers[("POST", "/pluribus/workflows/resolve")]
    workflow_ref = response_json(
        run(resolve(FakeRequest({"localWorkflowKey": "private-workflow"})))
    )["workflowRef"]
    source_resolve = prompt_server.routes.handlers[
        ("POST", "/pluribus/workflows/{workflow_ref}/sources/resolve")
    ]
    source_ref = response_json(
        run(
            source_resolve(
                FakeRequest(
                    {"localSourceKey": "nightmare-shadow.png", "sourceKind": "reference"},
                    {"workflow_ref": workflow_ref},
                )
            )
        )
    )["sourceRef"]
    put = prompt_server.routes.handlers[
        ("PUT", "/pluribus/workflows/{workflow_ref}/source-reviews/{source_ref}")
    ]
    get = prompt_server.routes.handlers[
        ("GET", "/pluribus/workflows/{workflow_ref}/source-reviews")
    ]

    saved = run(
        put(
            FakeRequest(
                {"state": "not_person", "sourceHash": "a" * 64},
                {"workflow_ref": workflow_ref, "source_ref": source_ref},
            )
        )
    )
    assert saved.status == 200
    assert response_json(saved) == {
        "review": {
            "sourceRef": source_ref,
            "state": "not_person",
            "sourceHash": "a" * 64,
        }
    }
    assert response_json(
        run(get(FakeRequest(None, {"workflow_ref": workflow_ref})))
    ) == {
        "reviews": [
            {
                "sourceRef": source_ref,
                "state": "not_person",
                "sourceHash": "a" * 64,
            }
        ]
    }


def test_source_links_route_sends_only_opaque_manifest(tmp_path, monkeypatch):
    prompt_server = FakePromptServer()
    captured = {}

    async def put_project_source_links(_connection_path, project_id, body):
        captured.update({"project_id": project_id, "body": body})
        return 200, {"project": {"id": project_id}}

    monkeypatch.setattr(remote, "put_project_source_links", put_project_source_links)
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=str(tmp_path / "connection.json"),
        bindings_path=str(tmp_path / "bindings.json"),
    )
    resolve = prompt_server.routes.handlers[("POST", "/pluribus/workflows/resolve")]
    workflow_ref = response_json(
        run(resolve(FakeRequest({"localWorkflowKey": "private-workflow", "graphHash": "a" * 64})))
    )["workflowRef"]
    source_resolve = prompt_server.routes.handlers[
        ("POST", "/pluribus/workflows/{workflow_ref}/sources/resolve")
    ]
    source_ref = response_json(
        run(
            source_resolve(
                FakeRequest(
                    {"localSourceKey": "/private/alex.png", "sourceKind": "reference"},
                    {"workflow_ref": workflow_ref},
                )
            )
        )
    )["sourceRef"]
    handler = prompt_server.routes.handlers[
        ("PUT", "/pluribus/projects/{project_id}/source-links")
    ]

    response = run(
        handler(
            FakeRequest(
                {
                    "workflowRef": workflow_ref,
                    "workflowKind": "storyboard",
                    "baseManifestVersion": 0,
                    "sources": [
                        {
                            "sourceRef": source_ref,
                            "sourceKind": "reference",
                            "sourceKey": "/private/alex.png",
                            "sourceNodeId": "12",
                            "prompt": "private prompt",
                            "disposition": "linked",
                            "talentRecordIds": ["talent-1"],
                            "operations": [
                                {"node_id": "44", "class_type": "IPAdapter"}
                            ],
                        }
                    ],
                },
                {"project_id": "project-1"},
            )
        )
    )

    assert response.status == 200
    assert captured["project_id"] == "project-1"
    assert captured["body"]["baseManifestVersion"] == 0
    outbound = json.dumps(captured["body"])
    assert "/private" not in outbound
    assert "private prompt" not in outbound
    assert "node_id" not in outbound
    assert captured["body"]["sources"][0]["operations"] == [
        {"classType": "IPAdapter"}
    ]
    synced_binding = response_json(
        run(resolve(FakeRequest({"localWorkflowKey": "private-workflow"})))
    )
    assert synced_binding["manifestHash"] == captured["body"]["manifestHash"]


def test_project_person_patch_route_forwards_current_project_identifiers(
    tmp_path, monkeypatch
):
    prompt_server = FakePromptServer()
    captured = {}

    async def update_project_person(
        _connection_path, project_id, person_id, body
    ):
        captured.update(
            {"project_id": project_id, "person_id": person_id, "body": body}
        )
        return 200, {"person": {"id": person_id}}

    monkeypatch.setattr(remote, "update_project_person", update_project_person)
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=str(tmp_path / "connection.json"),
        bindings_path=str(tmp_path / "bindings.json"),
    )
    handler = prompt_server.routes.handlers[
        ("PATCH", "/pluribus/projects/{project_id}/people/{person_id}")
    ]

    response = run(
        handler(
            FakeRequest(
                {
                    "displayName": "Alex Person",
                    "representative": {
                        "role": "manager",
                        "email": "rep@example.com",
                    },
                },
                {"project_id": "project-1", "person_id": "person-1"},
            )
        )
    )

    assert response.status == 200
    assert captured == {
        "project_id": "project-1",
        "person_id": "person-1",
        "body": {
            "displayName": "Alex Person",
            "representative": {
                "role": "manager",
                "email": "rep@example.com",
            },
        },
    }


def test_direct_project_person_new_and_replay_persist_verified_workspace_alias(
    tmp_path, monkeypatch
):
    bindings_path = str(tmp_path / "bindings.json")
    store = BindingStore(bindings_path)
    workflow = store.resolve_workflow("direct-person-workflow")
    store.associate(workflow["workflowRef"], "project-1", "production")
    source = store.resolve_source(
        workflow["workflowRef"], "person.png", "reference"
    )["sourceRef"]
    draft = store.put_person_draft(
        workflow["workflowRef"],
        {"displayName": "Alex", "sourceRefs": [source]},
    )
    calls = []

    async def create_project_person(_connection_path, project_id, body):
        calls.append((project_id, body))
        assert "workflowRef" not in body
        return 201, {
            "person": {"id": "33333333-3333-4333-8333-333333333333"}
        }

    monkeypatch.setattr(remote, "create_project_person", create_project_person)
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        connection_path=str(tmp_path / "connection.json"),
        bindings_path=bindings_path,
    )
    handler = prompt_server.routes.handlers[
        ("POST", "/pluribus/projects/{project_id}/people")
    ]
    body = {
        "mode": "new",
        "workflowRef": workflow["workflowRef"],
        "clientPersonId": draft["draftId"],
        "displayName": "Alex",
    }

    first = run(handler(FakeRequest(body, {"project_id": "project-1"})))
    replay = run(handler(FakeRequest(body, {"project_id": "project-1"})))

    assert first.status == 201
    assert replay.status == 201
    assert len(calls) == 2
    persisted = BindingStore(bindings_path).list_person_drafts(
        workflow["workflowRef"]
    )[0]
    assert persisted["canonicalPersonId"] == "33333333-3333-4333-8333-333333333333"
    assert persisted["workspaceAlias"]["requestMode"] == "new"
    assert persisted["workspaceAlias"]["clientPersonId"] == draft["draftId"]


def test_direct_existing_attach_persists_verified_workspace_alias(
    tmp_path, monkeypatch
):
    bindings_path = str(tmp_path / "bindings.json")
    store = BindingStore(bindings_path)
    workflow = store.resolve_workflow("direct-existing-workflow")
    store.associate(workflow["workflowRef"], "project-1", "production")
    source = store.resolve_source(
        workflow["workflowRef"], "person.png", "reference"
    )["sourceRef"]
    canonical_id = "33333333-3333-4333-8333-333333333333"
    draft = store.put_person_draft(
        workflow["workflowRef"],
        {
            "canonicalPersonId": canonical_id,
            "displayName": "Alex",
            "sourceRefs": [source],
        },
    )
    captured = {}

    async def create_project_person(_connection_path, _project_id, body):
        captured.update(body)
        return 200, {"person": {"id": canonical_id}}

    monkeypatch.setattr(remote, "create_project_person", create_project_person)
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        bindings_path=bindings_path,
    )
    handler = prompt_server.routes.handlers[
        ("POST", "/pluribus/projects/{project_id}/people")
    ]
    response = run(
        handler(
            FakeRequest(
                {
                    "mode": "existing",
                    "workflowRef": workflow["workflowRef"],
                    "clientPersonId": draft["draftId"],
                    "talentRecordId": canonical_id,
                },
                {"project_id": "project-1"},
            )
        )
    )

    assert response.status == 200
    assert captured == {
        "mode": "existing",
        "clientPersonId": draft["draftId"],
        "talentRecordId": canonical_id,
    }
    persisted = BindingStore(bindings_path).list_person_drafts(
        workflow["workflowRef"]
    )[0]
    assert persisted["workspaceAlias"]["requestMode"] == "existing"
    assert persisted["workspaceAlias"]["canonicalPersonId"] == canonical_id


def test_workspace_project_routes_are_registered(tmp_path):
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
    )
    expected = {
        ("GET", "/pluribus/workspace"),
        ("POST", "/pluribus/workspace"),
        ("GET", "/pluribus/projects"),
        ("POST", "/pluribus/projects"),
        ("GET", "/pluribus/projects/{project_id}"),
        ("POST", "/pluribus/projects/{project_id}/people"),
        ("PATCH", "/pluribus/projects/{project_id}/people/{person_id}"),
        ("PUT", "/pluribus/projects/{project_id}/source-links"),
        ("PUT", "/pluribus/projects/{project_id}/use"),
        ("POST", "/pluribus/projects/{project_id}/confirmation-requests"),
    }
    assert expected <= set(prompt_server.routes.handlers)


def test_identity_routes_return_visual_review_contract_without_vectors(tmp_path):
    class UnavailableAnalyzer:
        analyzer_id = "test_identity"
        model_version = "test-identity-v1"

        def status(self):
            return AnalyzerStatus(
                False,
                self.analyzer_id,
                self.model_version,
                (
                    {
                        "issueId": "identity_models_unavailable",
                        "severity": "warning",
                        "title": "Models unavailable",
                        "description": "Install local models.",
                    },
                ),
            )

        def analyze(self, *_args):
            raise AssertionError("unavailable analyzer must not run")

    media_root = tmp_path / "input"
    media_root.mkdir()
    image_path = media_root / "portrait.jpg"
    image_path.write_bytes(b"local-image-bytes")
    identity = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=UnavailableAnalyzer(),
        media_roots=[str(media_root)],
    )
    bindings_path = str(tmp_path / "bindings.json")
    bindings = BindingStore(bindings_path)
    workflow = bindings.resolve_workflow("little-flower-local")
    source = bindings.resolve_source(
        workflow["workflowRef"], "portrait.jpg", "reference"
    )
    prompt_server = FakePromptServer()
    register_routes(
        prompt_server,
        roster_path=None,
        actions_path=str(tmp_path / "invites.json"),
        bindings_path=bindings_path,
        identity_service=identity,
    )
    analyze = prompt_server.routes.handlers[("POST", "/pluribus/identity/analyze")]

    async def scenario():
        response = await analyze(
            FakeRequest(
                {
                    "workflowName": "Little Flower",
                    "workflowFingerprint": "a" * 64,
                    "workflowRef": workflow["workflowRef"],
                    "sources": [
                        {
                            "sourceRef": source["sourceRef"],
                            "sourceKey": "portrait.jpg",
                            "sourceKind": "reference",
                        }
                    ],
                }
            )
        )
        started = response_json(response)
        for _ in range(200):
            payload = identity.get_job(started["jobId"])
            if payload["state"] in {"completed", "failed", "canceled"}:
                return response, started, payload
            await asyncio.sleep(0.01)
        raise AssertionError("identity route job did not finish")

    response, started, payload = run(scenario())

    assert response.status == 202
    assert started["state"] in {"queued", "running"}
    assert started["jobId"] == payload["jobId"]
    assert payload["state"] == "completed"
    assert set(payload) >= {
        "jobId",
        "coverage",
        "candidates",
        "occurrences",
        "issues",
        "evidence",
    }
    assert payload["coverage"]["totalSources"] == 1
    assert payload["coverage"]["imageCount"] == 1
    assert '"embedding":' not in json.dumps(payload).lower()
    mismatched_source = run(
        analyze(
            FakeRequest(
                {
                    "workflowRef": workflow["workflowRef"],
                    "sources": [
                        {
                            "sourceRef": "c" * 64,
                            "sourceKey": "portrait.jpg",
                            "sourceKind": "reference",
                        }
                    ],
                }
            )
        )
    )
    assert mismatched_source.status == 400
    assert "minted" in response_json(mismatched_source)["message"]
    assert {
        ("GET", "/pluribus/identity/capabilities"),
        ("POST", "/pluribus/identity/models/install"),
        ("GET", "/pluribus/identity/jobs/{job_id}"),
        ("POST", "/pluribus/identity/jobs/{job_id}/cancel"),
        ("DELETE", "/pluribus/identity/jobs/{job_id}"),
        ("GET", "/pluribus/identity/jobs/{job_id}/evidence"),
        ("GET", "/pluribus/identity/jobs/{job_id}/evidence/{artifact_id}"),
        ("GET", "/pluribus/identity/jobs/{job_id}/links"),
        ("PUT", "/pluribus/identity/jobs/{job_id}/links"),
        ("DELETE", "/pluribus/identity/jobs/{job_id}/links"),
    } <= set(prompt_server.routes.handlers)

    artifact = tmp_path / "private-face.png"
    artifact.write_bytes(b"private-face-evidence")
    identity.artifact_path = lambda _job_id, _artifact_id: str(artifact)
    evidence_artifact = prompt_server.routes.handlers[
        ("GET", "/pluribus/identity/jobs/{job_id}/evidence/{artifact_id}")
    ]
    artifact_response = run(
        evidence_artifact(
            FakeRequest({}, {"job_id": "job-local", "artifact_id": "face.png"})
        )
    )

    assert artifact_response.headers["Cache-Control"] == "no-store"
