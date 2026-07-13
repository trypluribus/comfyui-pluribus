from __future__ import annotations

from aiohttp import web

from .bindings import BindingStore
from .api import (
    action_payload,
    packet_payload,
    replace_payload,
    roster_payload,
    scan_request_payload,
)
from .engine import ClearanceEngine
from .invites import (
    apply_status_updates,
    client_request_id_for_invite,
    normalize_scope_statements,
    read_actions,
    record_action,
)
from .roster import Roster
from . import remote


def register_routes(
    prompt_server,
    roster_path: str | None,
    actions_path: str,
    connection_path: str | None = None,
    bindings_path: str | None = None,
) -> None:
    routes = prompt_server.routes
    # A clean install has no fabricated talent or clearance state. Passing a
    # roster path remains available only for legacy fixtures and headless tests.
    engine = ClearanceEngine(Roster.from_json(roster_path) if roster_path else Roster([]))
    if connection_path is None:
        import os

        connection_path = os.path.join(
            os.path.dirname(actions_path) or ".", remote.CONNECTION_FILENAME
        )
    if bindings_path is None:
        import os

        bindings_path = os.path.join(
            os.path.dirname(actions_path) or ".", "bindings.json"
        )
    bindings = BindingStore(bindings_path)

    async def _body(request) -> dict:
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Request body must be an object.")
        return value

    def _path(request, key: str) -> str:
        value = getattr(request, "match_info", {}).get(key)
        if not value:
            raise ValueError(f"Missing route parameter: {key}.")
        return str(value)

    def _error(exc: ValueError):
        return web.json_response({"state": "validation_error", "message": str(exc)}, status=400)

    def _remote_response(result: tuple[int, dict]):
        status, payload = result
        return web.json_response(payload, status=status)

    @routes.post("/pluribus/scan")
    async def scan(request):
        # The scan is strictly local. It does not query the connected account
        # or infer a clearance decision from a fixture roster.
        return web.json_response(scan_request_payload(await request.json(), engine))

    @routes.get("/pluribus/roster")
    async def roster(request):
        return web.json_response(roster_payload(engine))

    @routes.post("/pluribus/replace")
    async def replace(request):
        return web.json_response(replace_payload(await request.json()))

    async def _handle_action(request):
        body = dict(await request.json())
        remote_result = None
        if body.get("kind", "invite") == "invite":
            body["client_request_id"] = client_request_id_for_invite(
                actions_path, body
            )
            existing_attempt = next(
                (
                    record
                    for record in read_actions(actions_path)
                    if record.get("kind") == "invite"
                    and record.get("client_request_id")
                    == body["client_request_id"]
                ),
                None,
            )
            prior_uncertain = bool(
                existing_attempt
                and existing_attempt.get("draft_reason")
                in {"in_flight", "unconfirmed"}
            )
            try:
                # Persist the exact frozen request before outbound I/O. A
                # process crash after the server/provider commits but before
                # the response returns can then reuse this UUID after restart.
                if existing_attempt is None and remote.read_connection(connection_path):
                    record_action(
                        actions_path,
                        "invite",
                        talent_id=body.get("talent_id"),
                        name=body.get("name", "Unknown"),
                        source_key=body.get("source_key", ""),
                        source_kind=body.get("source_kind", ""),
                        workflow_name=body.get("workflow_name", ""),
                        workflow_fingerprint=body.get("workflow_fingerprint", ""),
                        scope_statements=normalize_scope_statements(
                            body.get("scope_statements")
                        ),
                        email=body.get("email", ""),
                        note=body.get("note", ""),
                        delivery=body.get("delivery", ""),
                        draft_reason="in_flight",
                        client_request_id=body["client_request_id"],
                    )
            except Exception:
                return web.json_response(
                    {
                        "action": None,
                        "state": "error",
                        "message": (
                            "Could not save the invite request locally; "
                            "nothing was sent. Check the Pluribus data directory."
                        ),
                    },
                    status=500,
                )
            remote_result = await remote.push_invite(connection_path, body)
            if prior_uncertain and (
                remote_result is None
                or remote_result.get("state")
                not in {"sent", "unconfirmed"}
            ):
                remote_result = {
                    "state": "unconfirmed",
                    "message": (
                        "A prior attempt is still unconfirmed; reuse this "
                        "request ID and sync before creating another invite."
                    ),
                }
        payload = action_payload(body, actions_path, remote_result)
        status = 400 if payload.get("state") == "validation_error" else 200
        return web.json_response(payload, status=status)

    @routes.post("/pluribus/invite")
    async def invite(request):
        return await _handle_action(request)

    @routes.post("/pluribus/action")
    async def action(request):
        return await _handle_action(request)

    @routes.post("/pluribus/invites/sync")
    async def invites_sync(request):
        result = await remote.fetch_invite_statuses(connection_path)
        changed = 0
        if result["state"] == "ok":
            changed = apply_status_updates(actions_path, result["invites"])
        return web.json_response(
            {"state": result["state"], "updated": changed, "server_invites": len(result["invites"])}
        )

    @routes.post("/pluribus/packet")
    async def packet(request):
        return web.json_response(packet_payload(await request.json(), engine, actions_path))

    @routes.get("/pluribus/connect")
    async def connect_status(request):
        return web.json_response(remote.get_status(connection_path))

    @routes.post("/pluribus/connect/start")
    async def connect_start(request):
        return web.json_response(await remote.start_pairing())

    @routes.post("/pluribus/connect/poll")
    async def connect_poll(request):
        return web.json_response(await remote.poll_pairing(connection_path))

    @routes.post("/pluribus/connect/disconnect")
    async def connect_disconnect(request):
        return web.json_response(await remote.disconnect(connection_path))

    @routes.post("/pluribus/workflows/resolve")
    async def workflow_resolve(request):
        try:
            body = await _body(request)
            return web.json_response(
                bindings.resolve_workflow(
                    body.get("localWorkflowKey"), body.get("graphHash")
                )
            )
        except ValueError as exc:
            return _error(exc)

    @routes.put("/pluribus/workflows/{workflow_ref}")
    async def workflow_associate(request):
        try:
            body = await _body(request)
            return web.json_response(
                bindings.associate(
                    _path(request, "workflow_ref"),
                    body.get("projectId"),
                    body.get("workflowKind"),
                )
            )
        except ValueError as exc:
            return _error(exc)

    @routes.post("/pluribus/workflows/{workflow_ref}/sources/resolve")
    async def source_resolve(request):
        try:
            body = await _body(request)
            return web.json_response(
                bindings.resolve_source(
                    _path(request, "workflow_ref"),
                    body.get("localSourceKey"),
                    body.get("sourceKind"),
                )
            )
        except ValueError as exc:
            return _error(exc)

    @routes.get("/pluribus/workspace")
    async def workspace_get(request):
        return _remote_response(await remote.fetch_workspace(connection_path))

    @routes.post("/pluribus/workspace")
    async def workspace_post(request):
        try:
            return _remote_response(
                await remote.create_workspace(connection_path, await _body(request))
            )
        except ValueError as exc:
            return _error(exc)

    @routes.get("/pluribus/projects")
    async def projects_get(request):
        return _remote_response(await remote.fetch_projects(connection_path))

    @routes.post("/pluribus/projects")
    async def projects_post(request):
        try:
            return _remote_response(
                await remote.create_project(connection_path, await _body(request))
            )
        except ValueError as exc:
            return _error(exc)

    @routes.get("/pluribus/projects/{project_id}")
    async def project_get(request):
        try:
            project_id = _path(request, "project_id")
            workflow_ref = getattr(request, "query", {}).get("workflowRef")
            result = await remote.fetch_project(
                connection_path, project_id, workflow_ref
            )
        except ValueError as exc:
            return _error(exc)
        return _remote_response(result)

    @routes.post("/pluribus/projects/{project_id}/people")
    async def project_people_post(request):
        try:
            return _remote_response(
                await remote.create_project_person(
                    connection_path,
                    _path(request, "project_id"),
                    await _body(request),
                )
            )
        except ValueError as exc:
            return _error(exc)

    @routes.put("/pluribus/projects/{project_id}/source-links")
    async def project_source_links_put(request):
        try:
            body = await _body(request)
            workflow_ref = body.get("workflowRef")
            payload = bindings.source_links_payload(
                workflow_ref, _path(request, "project_id"), body
            )
            project_id = _path(request, "project_id")
            result = await remote.put_project_source_links(
                connection_path,
                project_id,
                payload,
            )
            if 200 <= result[0] < 300:
                bindings.record_source_links(workflow_ref, project_id, payload)
            return _remote_response(result)
        except ValueError as exc:
            return _error(exc)

    @routes.put("/pluribus/projects/{project_id}/use")
    async def project_use_put(request):
        try:
            return _remote_response(
                await remote.put_project_use(
                    connection_path,
                    _path(request, "project_id"),
                    await _body(request),
                )
            )
        except ValueError as exc:
            return _error(exc)

    @routes.post("/pluribus/projects/{project_id}/confirmation-requests")
    async def project_confirmation_post(request):
        try:
            return _remote_response(
                await remote.create_confirmation_request(
                    connection_path,
                    _path(request, "project_id"),
                    await _body(request),
                )
            )
        except ValueError as exc:
            return _error(exc)
