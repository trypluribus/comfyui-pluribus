# Pluribus for ComfyUI

**Rights scan, roster, and terms workflow inside ComfyUI.** Pluribus scans the
ComfyUI graph you already have — new project or existing — and surfaces
person-bearing sources it can derive from graph provenance and the local roster:
LoRAs, supported face adapters, reference images, and prompt terms. It is not
face recognition and cannot guarantee it detects every person in rendered
media. Connected users can send a terms invite; after the
recipient accepts, the next connected rescan shows **Terms accepted** for the
same workflow graph, source, and requested scope. That status is separate from
the roster's clearance state and still requires a scope review. Eligible roster
entries can be dropped into the canvas as nodes linked to the current roster
record.

Free under GPL-3.0, including client work. Detection is derived from graph
provenance and your roster — local and deterministic, with no face-recognition
model. The scan itself stays on the ComfyUI server; pairing and connected
invites transmit only the account and invite details described below.

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

No account is needed to scan or save a local invite draft. The local ComfyUI
path is verified; a cloud-hosted smoke test remains a launch gate. For read-only
plugin directories (common on cloud mounts), local action and draft records
fall back to a private per-user, per-install temp directory, or set
`PLURIBUS_DATA_DIR` to choose where they live. Treat each data directory as
single-process state; do not point two concurrent ComfyUI processes at the
same `PLURIBUS_DATA_DIR`.

## First scan

Open any workflow and hit **Scan** (the panel also auto-scans the workflow
loaded when it first opens; `Rescan` re-runs it). The scanner looks for:

- `LoraLoader` / `LoraLoaderModelOnly` `lora_name` inputs
- `LoadImage` references upstream of face/IPAdapter-style nodes
- prompt-only person terms in `CLIPTextEncode`
- standalone `PluribusSourceMarker` nodes

Every detected source gets a roster/scan state — Roster / scope on file, Needs
review, Restricted, Synthetic unverified, or Unidentified — plus detection
reticles drawn on the flagged nodes (clicking a card centers and selects its
node). Accepted invite terms appear as an additional **Terms accepted · scope
review still required** status; they do not silently change the underlying
roster state.

Want something to poke at before using your own graph? Load
`fixtures/pluribus_marker_workflow.json` to see several source-marker nodes at
once.

## Roster & the Talent Record node

The panel's **Roster** tab lists your local roster. Entries with scope on file
are draggable — drop one onto the canvas (or double-click the row) and it lands
as a `Pluribus · Talent Record` node linked to that roster identity. The node
emits a placeholder reference IMAGE plus the talent name; real reference assets
stay in Pluribus. On each scan, Pluribus looks up the current local roster
record and lists supported downstream graph nodes (each chip click focuses that
node). Review the recorded scope against the intended use.

## Invite for terms

**Invite for terms** opens the invite dialog (recipient, email, personal note,
Email invite / Copy link delivery). What sending does depends on whether you
are connected to Pluribus:

- **Not connected (default):** a local record is written to the data
  directory as a **draft only**. No accept code or hosted URL is created, no
  email is sent, nothing leaves your machine, and the person is not marked as
  invited.
- **Connected:** the invite goes through your Pluribus account — Pluribus
  creates the canonical accept code and URL. For Email delivery it emails the
  link to a required valid recipient address (reply-to is your account email);
  it never silently converts a missing/invalid email into Copy link. For Copy
  link it returns a shareable URL. If browser clipboard access is unavailable,
  the dialog stays open with the full link for manual copying. Only a confirmed
  server response marks the person invited in the current session.
- **Transport failure or server 5xx:** the local record is **unconfirmed**, not
  failed — the server may already have committed or emailed it. Retrying the
  identical request is safe because the browser pre-mints one
  `client_request_id` before the first attempt and carries it through retries;
  request fields freeze before sending, and the local server reuses a matching
  unconfirmed draft's ID even after the dialog is closed or reloaded. A later
  sync can reconcile that draft with the server record.
- **Email delivery ambiguity:** if Pluribus reports the provider attempt as
  `ambiguous` or `in_flight`, the email may already have been sent. Retry or
  sync the same invite and existing link; do not create a replacement invite.
  `manual_reconciliation` disables automatic resend because an operator must
  compare the invite with provider delivery records first.
- **Expired connection or definite validation/server rejection:** the attempt
  creates no hosted URL. Reconnect or correct the request before retrying.

Every connected **Rescan** waits for server status sync before scanning. Sync
also runs when the panel mounts, and a changed record triggers a fresh scan.
**Terms accepted** appears and the invite action disappears only when workflow
name, canonical API-graph fingerprint, source identity, and the exact scope
statements stored on the accepted invite all match. The fingerprint is a SHA-256
of canonical API graph JSON and is intentionally strict: **any execution-graph
change invalidates the match**, even when the currently displayed terms would
otherwise look unchanged. Loading a workflow clears stale scan results and
reticles; action buttons also verify the live graph before proceeding.
Acceptance is evidence that those invite terms were accepted, not a
legal-clearance determination or an automatic approval for every use.

Pre-fingerprint invite records from earlier plugin versions remain in the
local audit history but conservatively do **not** display Terms accepted or
remove the invite action. Migrating/reissuing those old records is a safe P2;
the plugin will not guess that an incomplete record matches the current graph.

**Details** expands recorded scope fields (allowed ✓ / prohibited ✕, conflicts,
union/rep, provenance path) and holds the secondary actions: `Replace with
roster source` (swaps a matching `image`/`lora_name` widget with a
kind-compatible roster asset whose scope is on file), `Identify source`, and
`Flag for review`.

## Connect to Pluribus (optional)

**Connect** in the panel header links this ComfyUI to your Pluribus account:
the plugin shows a short pairing code, you approve it at
[trypluribus.com/pair](https://trypluribus.com/pair) (email sign-in, no
password), and the plugin receives its own API token — stored locally in the
data directory. **Disconnect** clears it only after the server confirms
revocation (or that it is already invalid); while offline, the token stays
local so revocation can be retried. The local path is verified; cloud-hosted
verification remains a launch gate. Scanning, the local roster, and local
drafts work while disconnected; invite delivery, shareable links, and
acceptance sync require a working connection. Starting pairing
contacts the configured Pluribus server, and approving the code associates that
plugin installation with your account.

Self-hosting or testing against a staging server? Point the plugin at it with
`PLURIBUS_SERVER_URL` (defaults to `https://trypluribus.com`).

## Scope — what this does NOT do

- Identity is inferred from graph provenance and the roster, **not** a vision
  model; a scan is a consent record aid, not a legal clearance determination.
- Synthetic outputs say "no known real-person source detected in graph"; they
  never claim "no NIL required."
- Restricted means a restriction is on file — review it against the campaign
  before use.
- Roster, action, invite, and draft data are local JSON while disconnected.
  Pairing exchanges the connection request and token with the configured
  Pluribus server. A connected invite sends the recipient name/email, note,
  proposed scope, source kind/key, workflow name, and workflow fingerprint so
  the hosted record can be delivered and matched during acceptance sync. It
  does not upload raw graph JSON, prompts, model files, or image files.

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
APIs, hosted terms workflows, and Pluribus trademarks are not licensed by
this plugin license (see [NOTICE](NOTICE)).

Pluribus is the talent layer for AI media — roster, rights, and consent where
the work is actually made. More at
[trypluribus.com](https://trypluribus.com/?src=comfyui).
