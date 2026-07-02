# Pluribus for ComfyUI — Rights scan

ComfyUI custom node package that scans the current workflow graph for person
sources. It derives source state from graph provenance and a local seed roster,
not face recognition. The UI follows the Claude Design mockups in
`docs/design/comfyui-plugin/` (CS4 rights scan + CS5 invite, "Refined Operator"
amber identity).

States: Cleared, Needs review, Restricted, Synthetic unverified, Unidentified.

## Demo without ComfyUI

```bash
cd comfyui-pluribus
python -m pluribus.scan_cli
```

This scans `fixtures/morning_people_spot_workflow_api.json` from the terminal. It is a
fallback for testing the scanner without ComfyUI. The matching UI-format graph
(`fixtures/morning_people_spot_workflow.json`) is the "Morning People" demo
spot — see `docs/design/comfyui-plugin/demo-workflow.md` for what it does and
what to plug in to render it for real.

## Use in ComfyUI

Symlink this folder into ComfyUI:

```bash
ln -s "$(pwd)" /path/to/ComfyUI/custom_nodes/comfyui-pluribus
```

Restart ComfyUI and open the **Pluribus** tab in the sidebar rail (older
frontends without the sidebar-tab API get a floating `Pluribus` launcher
bottom-right instead). The panel auto-scans the workflow currently loaded in
ComfyUI on first open; `Rescan` re-runs it.

A marker-only test workflow is included at:

```text
fixtures/pluribus_marker_workflow.json
```

Load that workflow in ComfyUI to see several `Pluribus Source Marker` nodes at
once (they render with the amber Pluribus node identity).

The scanner looks for:

- `LoraLoader` / `LoraLoaderModelOnly` `lora_name` inputs
- `LoadImage` references upstream of face/IPAdapter-style nodes
- prompt-only person terms in `CLIPTextEncode`
- standalone `PluribusSourceMarker` nodes

## Roster & the Cleared Talent node (inception path)

The panel has two tabs. **Roster** lists the local roster: cleared talent are
draggable — drop one onto the canvas (or double-click the row) and it lands as
a `Pluribus · Cleared Talent` node (amber identity, striped likeness thumb,
"twin tracked · consent live" footer). The node emits a placeholder reference
IMAGE plus the talent name; real reference assets stay in Pluribus. Scans
classify talent nodes against the roster and list the downstream operations
performed on the twin ("performance altered by" — each chip click focuses
that node in the graph). `GET /pluribus/roster` serves the roster.

## Panel

- Summary tiles: sources found / need action / synthetic, plus a warning strip
  when anything needs action before the output is clear to use.
- Detection reticles: corner brackets drawn on flagged nodes in the graph,
  colored by state. Clicking a card centers and selects its node.
- **Invite to clear** opens the invite dialog (recipient, email, personal note,
  Email invite / Copy link delivery). Sending writes a local record to
  `data/invites.json` with a generated single-use-style accept code and a
  placeholder `pluribus.so/accept/…` URL — no email is sent and nothing leaves
  the machine.
- **Details** expands consent scope (allowed ✓ / prohibited ✕, conflicts,
  union/rep, provenance path) and holds the secondary actions:
  - `Replace with cleared` swaps a matching `image` or `lora_name` widget with
    a kind-compatible cleared asset from the local roster.
  - `Identify source` records that an unknown reference needs attribution.
  - `Flag for review` records an internal review flag.

## Frontend layout (`web/`)

Vanilla ES modules, no build step: `pluribus.js` (entry + sidebar tab +
fallback), `pluribus.css` (identity tokens + components), `panel.js`,
`invite.js`, `canvas.js` (reticles/focus), `components.js`, `store.js`,
`api.js`.

## Tests

```bash
cd comfyui-pluribus
python -m pytest -v
```

## Scope

- Identity is inferred from graph provenance and the seed roster, not a vision model.
- Synthetic outputs say "no known real-person source detected in graph"; they never claim "no NIL required."
- Restricted means there is a restriction on file and BA should review against the campaign before use.
- Roster, action, and invite data are local JSON only; accept URLs are
  placeholders until the web app ships a real accept flow.
- There is no Next.js, Supabase, Notion, Gmail, PDF, ML recognition, or live conflict-analysis integration.

## License

GPL-3.0-only. See `LICENSE`.

This license covers the local plugin in this repository only. Pluribus
server-side services, roster and consent APIs, clearance workflows, and
the Pluribus name and marks are not licensed by it. Substantial code
contributions require a CLA — see `CONTRIBUTING.md`.
