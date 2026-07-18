from __future__ import annotations

import os
from functools import wraps
from urllib.parse import urlsplit

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
from .identity_service import (
    IdentityAnalysisService,
    IdentityCapacityError,
    IdentityConflictError,
)
from . import remote


def register_routes(
    prompt_server,
    roster_path: str | None,
    actions_path: str,
    connection_path: str | None = None,
    bindings_path: str | None = None,
    identity_service: IdentityAnalysisService | None = None,
) -> None:
    routes = prompt_server.routes
    # A clean install has no fabricated talent or clearance state. Passing a
    # roster path remains available only for legacy fixtures and headless tests.
    engine = ClearanceEngine(Roster.from_json(roster_path) if roster_path else Roster([]))
    if connection_path is None:
        connection_path = os.path.join(
            os.path.dirname(actions_path) or ".", remote.CONNECTION_FILENAME
        )
    if bindings_path is None:
        bindings_path = os.path.join(
            os.path.dirname(actions_path) or ".", "bindings.json"
        )
    bindings = BindingStore(bindings_path)
    identity = identity_service or IdentityAnalysisService(
        os.path.dirname(actions_path) or "."
    )

    async def _body(request) -> dict:
        content_type = str(getattr(request, "content_type", "") or "").lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            raise ValueError("Request Content-Type must be application/json.")
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Request body must be an object.")
        return value

    def _same_origin(request) -> bool:
        headers = getattr(request, "headers", {}) or {}
        if str(headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return False
        origin = str(headers.get("Origin") or "").strip()
        if not origin:
            # Non-browser clients and same-origin requests may omit Origin.
            return True
        host = str(getattr(request, "host", "") or headers.get("Host") or "")
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return (
            parsed.scheme in {"http", "https"}
            and bool(host)
            and parsed.netloc.lower() == host.lower()
        )

    def _mutation_route(registrar, path: str):
        """Reject browser cross-site access before a protected local route."""

        def decorator(handler):
            @wraps(handler)
            async def guarded(request):
                if not _same_origin(request):
                    return web.json_response(
                        {
                            "state": "forbidden",
                            "message": "Cross-site Pluribus requests are not allowed.",
                        },
                        status=403,
                        headers={"Cache-Control": "no-store", "Vary": "Origin"},
                    )
                return await handler(request)

            return registrar(path)(guarded)

        return decorator

    def _identity_response(payload: object, *, status: int = 200, headers=None):
        response_headers = {
            "Cache-Control": "no-store",
            "Vary": "Origin",
            **(headers or {}),
        }
        return web.json_response(payload, status=status, headers=response_headers)

    def _validated_identity_request(body: dict) -> dict:
        workflow_ref = body.get("workflowRef", body.get("workflow_ref"))
        bindings.get(workflow_ref)
        sources = body.get("sources")
        if not isinstance(sources, list):
            raise ValueError("sources must be a list.")
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("Each identity source must be an object.")
            source_kind = str(
                source.get("sourceKind") or source.get("source_kind") or "unknown"
            )
            source_key = str(
                source.get("sourceKey") or source.get("source_key") or ""
            )
            if not source_key:
                node_id = str(
                    source.get("sourceNodeId")
                    or source.get("source_node_id")
                    or source.get("outputNodeId")
                    or source.get("output_node_id")
                    or ""
                )
                source_key = f"{source_kind}:{node_id}"
            source_ref = source.get("sourceRef", source.get("source_ref"))
            if not bindings.source_matches_workflow(
                workflow_ref, source_ref, source_key, source_kind
            ):
                raise ValueError(
                    "Each identity source must match the opaque sourceRef minted "
                    "for this workflow and local source slot."
                )
        return body

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

    def _identity_not_found_or_error(exc: ValueError):
        status = (
            429
            if isinstance(exc, IdentityCapacityError)
            else 409
            if isinstance(exc, IdentityConflictError)
            else 404
            if "not found" in str(exc).lower()
            else 400
        )
        return _identity_response(
            {"state": "validation_error", "message": str(exc)}, status=status
        )

    @_mutation_route(routes.post, "/pluribus/scan")
    async def scan(request):
        # The scan is strictly local. It does not query the connected account
        # or infer a clearance decision from a fixture roster.
        try:
            return web.json_response(scan_request_payload(await _body(request), engine))
        except ValueError as exc:
            return _error(exc)

    @routes.get("/pluribus/identity/capabilities")
    async def identity_capabilities(request):
        return _identity_response(identity.capabilities())

    @_mutation_route(routes.post, "/pluribus/identity/models/install")
    async def identity_models_install(request):
        try:
            return _identity_response(await identity.install_models(await _body(request)))
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.post, "/pluribus/identity/analyze")
    async def identity_analyze(request):
        try:
            body = _validated_identity_request(await _body(request))
            payload = await identity.start_job(body)
            status = 200 if payload.get("state") == "completed" else 202
            return _identity_response(payload, status=status)
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.get, "/pluribus/identity/jobs/{job_id}")
    async def identity_job_get(request):
        try:
            return _identity_response(identity.get_job(_path(request, "job_id")))
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.post, "/pluribus/identity/jobs/{job_id}/cancel")
    async def identity_job_cancel(request):
        try:
            return _identity_response(
                await identity.cancel_job(_path(request, "job_id"))
            )
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.delete, "/pluribus/identity/jobs/{job_id}")
    async def identity_job_delete(request):
        try:
            return _identity_response(
                await identity.delete_job(_path(request, "job_id"))
            )
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.get, "/pluribus/identity/jobs/{job_id}/evidence")
    async def identity_evidence_manifest(request):
        try:
            return _identity_response(
                identity.evidence_manifest(_path(request, "job_id")),
            )
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(
        routes.get, "/pluribus/identity/jobs/{job_id}/evidence/{artifact_id}"
    )
    async def identity_evidence_artifact(request):
        try:
            artifact_path = identity.artifact_path(
                _path(request, "job_id"), _path(request, "artifact_id")
            )
            return web.FileResponse(
                artifact_path,
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'none'",
                },
            )
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.get, "/pluribus/identity/jobs/{job_id}/links")
    async def identity_links_get(request):
        try:
            return _identity_response(identity.get_links(_path(request, "job_id")))
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.put, "/pluribus/identity/jobs/{job_id}/links")
    async def identity_links_put(request):
        try:
            return _identity_response(
                identity.put_links(
                    _path(request, "job_id"), await _body(request)
                )
            )
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @_mutation_route(routes.delete, "/pluribus/identity/jobs/{job_id}/links")
    async def identity_links_delete(request):
        try:
            return _identity_response(
                identity.delete_links(
                    _path(request, "job_id"), await _body(request)
                )
            )
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @routes.get("/pluribus/roster")
    async def roster(request):
        return web.json_response(roster_payload(engine))

    @_mutation_route(routes.post, "/pluribus/replace")
    async def replace(request):
        try:
            return web.json_response(replace_payload(await _body(request)))
        except ValueError as exc:
            return _error(exc)

    async def _handle_action(request):
        try:
            body = dict(await _body(request))
        except ValueError as exc:
            return _error(exc)
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

    @_mutation_route(routes.post, "/pluribus/invite")
    async def invite(request):
        return await _handle_action(request)

    @_mutation_route(routes.post, "/pluribus/action")
    async def action(request):
        return await _handle_action(request)

    @_mutation_route(routes.post, "/pluribus/invites/sync")
    async def invites_sync(request):
        result = await remote.fetch_invite_statuses(connection_path)
        changed = 0
        if result["state"] == "ok":
            changed = apply_status_updates(actions_path, result["invites"])
        return web.json_response(
            {"state": result["state"], "updated": changed, "server_invites": len(result["invites"])}
        )

    @_mutation_route(routes.post, "/pluribus/packet")
    async def packet(request):
        try:
            return web.json_response(
                packet_payload(await _body(request), engine, actions_path)
            )
        except ValueError as exc:
            return _error(exc)

    @routes.get("/pluribus/connect")
    async def connect_status(request):
        return web.json_response(remote.get_status(connection_path))

    @_mutation_route(routes.post, "/pluribus/connect/start")
    async def connect_start(request):
        return web.json_response(await remote.start_pairing())

    @_mutation_route(routes.post, "/pluribus/connect/poll")
    async def connect_poll(request):
        return web.json_response(await remote.poll_pairing(connection_path))

    @_mutation_route(routes.post, "/pluribus/connect/disconnect")
    async def connect_disconnect(request):
        return web.json_response(await remote.disconnect(connection_path))

    @_mutation_route(routes.post, "/pluribus/workflows/resolve")
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

    @_mutation_route(routes.put, "/pluribus/workflows/{workflow_ref}")
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

    @_mutation_route(routes.post, "/pluribus/workflows/{workflow_ref}/sources/resolve")
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

    @routes.get("/pluribus/workflows/{workflow_ref}/person-drafts")
    async def person_drafts_get(request):
        try:
            source_ref = getattr(request, "query", {}).get("sourceRef")
            return web.json_response(
                {
                    "drafts": bindings.list_person_drafts(
                        _path(request, "workflow_ref"), source_ref
                    )
                }
            )
        except ValueError as exc:
            return _error(exc)

    @_mutation_route(routes.put, "/pluribus/workflows/{workflow_ref}/person-drafts")
    async def person_drafts_put(request):
        try:
            return web.json_response(
                {
                    "draft": bindings.put_person_draft(
                        _path(request, "workflow_ref"), await _body(request)
                    )
                }
            )
        except ValueError as exc:
            return _error(exc)

    @_mutation_route(routes.delete, "/pluribus/workflows/{workflow_ref}/person-drafts/{draft_id}")
    async def person_drafts_delete(request):
        try:
            workflow_ref = _path(request, "workflow_ref")
            draft_id = _path(request, "draft_id")
            draft = next(
                (
                    value
                    for value in bindings.list_person_drafts(workflow_ref)
                    if value.get("draftId") == draft_id
                ),
                None,
            )
            if draft:
                draft_person_ids = {
                    str(value)
                    for value in (
                        draft.get("draftId"),
                        draft.get("canonicalPersonId"),
                    )
                    if value
                }
                with identity.guard_unlinked_person_ids(
                    workflow_ref, draft_person_ids
                ):
                    deleted = bindings.delete_person_draft(workflow_ref, draft_id)
            else:
                deleted = bindings.delete_person_draft(workflow_ref, draft_id)
            return web.json_response({"deleted": deleted, "draftId": draft_id})
        except ValueError as exc:
            return _identity_not_found_or_error(exc)

    @routes.get("/pluribus/workflows/{workflow_ref}/source-reviews")
    async def source_reviews_get(request):
        try:
            return web.json_response(
                {
                    "reviews": bindings.list_source_reviews(
                        _path(request, "workflow_ref")
                    )
                }
            )
        except ValueError as exc:
            return _error(exc)

    @_mutation_route(
        routes.put, "/pluribus/workflows/{workflow_ref}/source-reviews/{source_ref}"
    )
    async def source_reviews_put(request):
        try:
            return web.json_response(
                {
                    "review": bindings.put_source_review(
                        _path(request, "workflow_ref"),
                        _path(request, "source_ref"),
                        await _body(request),
                    )
                }
            )
        except ValueError as exc:
            return _error(exc)

    @routes.get("/pluribus/workspace")
    async def workspace_get(request):
        return _remote_response(await remote.fetch_workspace(connection_path))

    @_mutation_route(routes.post, "/pluribus/workspace")
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

    @_mutation_route(routes.post, "/pluribus/projects")
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

    @_mutation_route(routes.post, "/pluribus/projects/{project_id}/people")
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

    @_mutation_route(routes.put, "/pluribus/projects/{project_id}/source-links")
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

    @_mutation_route(routes.put, "/pluribus/projects/{project_id}/use")
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

    @_mutation_route(
        routes.post, "/pluribus/projects/{project_id}/confirmation-requests"
    )
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
