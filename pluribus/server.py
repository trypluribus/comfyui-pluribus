from __future__ import annotations

from aiohttp import web

from .api import (
    action_payload,
    invite_payload,
    packet_payload,
    replace_payload,
    roster_payload,
    scan_payload,
)
from .engine import ClearanceEngine
from .roster import Roster


def register_routes(prompt_server, roster_path: str, actions_path: str) -> None:
    routes = prompt_server.routes
    engine = ClearanceEngine(Roster.from_json(roster_path))

    @routes.post("/pluribus/scan")
    async def scan(request):
        return web.json_response(scan_payload(await request.json(), engine))

    @routes.get("/pluribus/roster")
    async def roster(request):
        return web.json_response(roster_payload(engine))

    @routes.post("/pluribus/replace")
    async def replace(request):
        return web.json_response(replace_payload(await request.json()))

    @routes.post("/pluribus/invite")
    async def invite(request):
        return web.json_response(invite_payload(await request.json(), actions_path))

    @routes.post("/pluribus/action")
    async def action(request):
        return web.json_response(action_payload(await request.json(), actions_path))

    @routes.post("/pluribus/packet")
    async def packet(request):
        return web.json_response(packet_payload(await request.json(), engine, actions_path))
