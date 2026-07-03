# Pluribus for ComfyUI

**Rights scan, roster, and clearance inside your workflow.** Pluribus scans the
ComfyUI graph you already have — new project or existing — and surfaces every
real and synthetic person feeding the output: LoRAs, face adapters, reference
images, prompt terms. Real people get invited to clear terms; cleared talent
drops into the canvas as a node with consent attached.

Free under GPL-3.0, including client work. Detection is derived from graph
provenance and your roster — local and deterministic, no face-recognition
model, and nothing leaves your machine.

## Install

Clone into your ComfyUI `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/trypluribus/comfyui-pluribus
```

Restart ComfyUI. The **Pluribus** tab appears in the sidebar rail (older
frontends without the sidebar-tab API get a floating `Pluribus` launcher
bottom-right instead). A Comfy Registry listing (`comfy node install
comfyui-pluribus`) is on the way.

No account is needed to scan. Works with local and cloud-hosted ComfyUI — if
the plugin directory is read-only (common on cloud mounts), invite records
fall back to a temp directory, or set `PLURIBUS_DATA_DIR` to choose where they
live.

## First scan

Open any workflow and hit **Scan** (the panel also auto-scans the workflow
loaded when it first opens; `Rescan` re-runs it). The scanner looks for:

- `LoraLoader` / `LoraLoaderModelOnly` `lora_name` inputs
- `LoadImage` references upstream of face/IPAdapter-style nodes
- prompt-only person terms in `CLIPTextEncode`
- standalone `PluribusSourceMarker` nodes

Every detected person gets a state — Cleared, Needs review, Restricted,
Synthetic unverified, or Unidentified — plus detection reticles drawn on the
flagged nodes (clicking a card centers and selects its node).

Want something to poke at before using your own graph? Load
`fixtures/pluribus_marker_workflow.json` to see several source-marker nodes at
once.

## Roster & the Cleared Talent node

The panel's **Roster** tab lists your local roster. Cleared talent are
draggable — drop one onto the canvas (or double-click the row) and it lands as
a `Pluribus · Cleared Talent` node with consent attached. The node emits a
placeholder reference IMAGE plus the talent name; real reference assets stay
in Pluribus. From that moment, scans list every downstream operation performed
on the twin ("performance altered by" — each chip click focuses that node).

## Invite to clear

**Invite to clear** opens the invite dialog (recipient, email, personal note,
Email invite / Copy link delivery). Today, sending writes a local record to
the data directory with a generated single-use accept code and a
`trypluribus.com/accept/…` link — **no email is sent and nothing leaves your
machine**. Invite delivery and the hosted accept flow are in development;
locally recorded codes are honored when they ship.

**Details** expands consent scope (allowed ✓ / prohibited ✕, conflicts,
union/rep, provenance path) and holds the secondary actions: `Replace with
cleared` (swaps a matching `image`/`lora_name` widget with a kind-compatible
cleared asset from the roster), `Identify source`, and `Flag for review`.

## Scope — what this does NOT do

- Identity is inferred from graph provenance and the roster, **not** a vision
  model; a scan is a consent record aid, not a legal clearance determination.
- Synthetic outputs say "no known real-person source detected in graph"; they
  never claim "no NIL required."
- Restricted means a restriction is on file — review it against the campaign
  before use.
- Roster, action, and invite data are local JSON; nothing syncs anywhere yet.

## Try it without ComfyUI

```bash
python -m pluribus.scan_cli
```

Scans the bundled "Morning People" demo spot
(`fixtures/morning_people_spot_workflow_api.json`) from the terminal.

## Development

For hacking on the plugin itself, a symlink beats cloning:

```bash
ln -s "$(pwd)" /path/to/ComfyUI/custom_nodes/comfyui-pluribus
```

Frontend is vanilla ES modules under `web/`, no build step. Tests:

```bash
python -m pytest -v
```

## License

GPL-3.0-only — see [LICENSE](LICENSE). Server-side Pluribus services, roster
APIs, hosted clearance workflows, and Pluribus trademarks are not licensed by
this plugin license (see [NOTICE](NOTICE)).

Pluribus is the talent layer for AI media — roster, rights, and consent where
the work is actually made. More at
[trypluribus.com](https://trypluribus.com/?src=comfyui).
