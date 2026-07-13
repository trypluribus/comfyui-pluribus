# Pluribus for ComfyUI

Pluribus adds a people-and-permission workflow beside the ComfyUI graph you are
already building. It finds supported person-bearing inputs locally, lets a
connected user link each source to the real people in a Pluribus project,
records the intended use, and creates a secure confirmation request for each
person or representative.

The normal v0.4 flow is:

> **Find people → Link people → Set intended use → Request confirmation → Track the recipient response separately from internal review**

A clean production install starts with no demo talent, no fixture permissions,
and no pre-cleared people. Test fixtures still exist for automated development
tests, but they are not loaded by the ComfyUI runtime.

> **Release status:** this repository contains the v0.4.0 implementation. The
> complete loop passed locally on 2026-07-13 from the launch branch against
> localhost and disposable local Supabase. It has not been certified from a
> public v0.4.0 tag against the deployed production API. See
> [Your first project](docs/first-project.md)
> for the user workflow and the exact verification boundary.

## What the plugin does

- Scans the current graph locally for supported reference images, identity
  LoRAs, face-adapter paths, person-like prompts, and explicit Pluribus source
  markers.
- Creates or selects a canonical Pluribus project for a real client or company.
- Associates the current ComfyUI workflow with that project as a character
  sheet, storyboard, production graph, final graph, or other workflow.
- Links one source to one or many people, and one person to many sources.
- Records a structured, versioned use brief: deliverables, channels, platforms,
  territory, term, paid and organic use, product category, AI actions,
  restrictions, compensation handling, exclusivity handling, and final-creative
  approval.
- Sends a secure confirmation request by email or returns a link to copy.
- Shows three separate status axes: the source/person link, the recipient
  request and response, and internal review.

The plugin does not generate a storyboard or character sheet. It groups the
ComfyUI workflows that you create into one rights context so the people and use
remain understandable across pre-production, production, and final review.

## Install

Clone the plugin into the ComfyUI `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/trypluribus/comfyui-pluribus
```

Restart ComfyUI. The **Pluribus** tab appears in the sidebar rail. Older
frontends without the sidebar-tab API get a floating **Pluribus** launcher in
the lower-right corner.

Run ComfyUI on loopback or behind authentication you trust. The plugin's
same-origin `/pluribus/*` routes can read and mutate the paired workspace using
the device token; exposing an otherwise unauthenticated ComfyUI server to a LAN
or the public internet also exposes that local proxy surface.

For a pinned production install, use a release tag only after that tag is
published and listed in the repository releases. A Comfy Registry listing is a
separate distribution follow-up.

If the plugin directory is read-only, set `PLURIBUS_DATA_DIR` to a private,
writable directory before starting ComfyUI. Otherwise, the plugin tries its
private `data/` directory and then a per-user temporary location. Do not point
two running ComfyUI processes at the same data directory.

## First use

No account is required for the local scan. A connection is required only when
you create/select canonical workspace records, link people remotely, save
intended use, or request confirmation.

### 1. Find people locally

Open a workflow and choose **Find people**. The panel also scans the workflow
that is open when the panel first mounts; **Rescan** repeats the scan.

Detection currently understands:

- `LoraLoader` and `LoraLoaderModelOnly` identity-model inputs;
- `LoadImage` references, including references upstream of supported
  face-adapter and image-editing nodes;
- person-like text in `CLIPTextEncode` when no stronger source is present;
- standalone `Pluribus Source Marker` nodes.

When adding a marker from the node library, choose the result once; a
double-click can insert two nodes. Reference, LoRA, and unknown markers require
a stable local source key. Prompt-only markers may omit the key when they have
a display name or note. Incomplete markers are ignored and shown as a local
warning, not counted as detected people.

For source-key and display-name rows, current ComfyUI builds open a small
**Value** editor; enter the value and choose **OK**. Save the workflow after its
first Pluribus scan/project binding as well as after marker edits so the private
workflow key is serialized with the graph.

The scanner follows graph provenance. It does not inspect pixels, recognize a
face, or guarantee that every person in a rendered output was found. Review
every result, especially custom-node graphs.

### 2. Connect and choose a project

Choose **Connect**. The plugin shows a short code and opens
[trypluribus.com/pair](https://trypluribus.com/pair). Sign in by email, enter
the code, and approve the connection.

Pairing creates a device-specific token; it does not create a project or upload
the graph. A new self-serve user can explicitly create an individual production
workspace. Organization access remains invitation-controlled, and the v0.4
setup dialog does not yet include an organization-workspace picker.

Create a project with a project name, real client/company, optional agency, and
short production context. Then classify the current graph as one of:

- Character sheet
- Storyboard
- Production
- Final
- Other

The plugin keeps a private stable workflow identifier so the same local graph
can be found after a restart without sending its filename or raw JSON.

### 3. Link every source to people

Open a detected source and choose **Link people**. You can select an existing
person in the project or add a new person with:

- name;
- project role;
- optional direct email;
- optional representative role, name, and email.

You can link a group image to multiple people. You can also link multiple
reference images, LoRAs, or prompt sources to the same person. If the detector
found something that is not a real person, mark it **Not a person**. If the
classification is unresolved, keep it **Review required**.

Adding or linking a person does not assert permission, representative
authority, or legal clearance.

### 4. Set intended use

Open **Intended use** and review the complete request before saving it. At a
minimum, record:

- the proposed use and deliverables;
- channels and any named platforms;
- organic and paid-media treatment;
- territory and usage dates;
- product category and languages;
- whether final-creative approval is required;
- whether compensation and exclusivity are included, handled separately, or
  outside this request;
- each person's restrictions, compensation, usage comfort, and representative
  authority notes;
- the revocation/takedown instructions, expected response time, and any
  model-disablement or platform-removal requirement.

Per-person fields are loaded from the canonical project record. They remain
separate when two people have different terms; saving one project brief does
not flatten those records into a shared restriction or compensation value.
Paid media or external activation requires an actionable revocation path
before the plugin will save the brief.

Supported downstream ComfyUI operation classes are translated into structured
AI-action rows, such as face editing, reference-image processing, identity
model generation, or image-to-video rendering. This mapping is an aid, not a
substitute for human review. Unsupported custom nodes may require a manual
follow-up outside the plugin.

Saving the form creates or updates the canonical versioned permission scope in
Pluribus.

### 5. Request confirmation

Open **People**, choose a linked person, and select **Request confirmation**.
Review the exact project and scope preview. It includes category, languages,
the selected person's terms and caveats, inferred AI actions, and the
revocation/takedown controls. Then provide:

- recipient name and email;
- expected recipient role;
- optional message;
- email delivery or copy-link delivery.

The recipient confirms their own role and authority on the secure review page.
They can confirm, confirm with a caveat, request changes, decline, state that
they lack authority, or exclude the person from the project.

Each create attempt uses a client-generated request ID. If delivery is pending
or ambiguous, keep the same request and reconcile it; do not create a second
request merely because an email response was lost. Email requests are limited
to 30 per workspace/user per hour.

**Retry same request** keeps the ID while the request dialog stays open. A
reload currently loses that dialog-held ID and cannot safely re-reveal an
already-created link-only URL; refresh canonical project state and reconcile
instead of blindly creating another request.

### 6. Read the statuses literally

The panel keeps three concepts separate:

- **Person/source:** whether the detected source is linked, not a person, or
  still needs review.
- **Request:** ready, pending, confirmed, confirmed with caveat, changes
  requested, declined, no authority, excluded, expired, or cancelled.
- **Internal review:** the production team's separate decision state.

A recipient response never becomes **Cleared**. In v0.4, the plugin reads the
recipient state but does not provide an internal approval action. The internal
axis separately reflects the current-scope canonical state: **Not reviewed**,
**Pending**, **Approved**, **Changes requested**, **Blocked**, or **Not
required**. Only an internal workspace decision can change that axis.

## What changes invalidate a confirmation context

Pluribus tracks two hashes for different purposes:

- The **rights manifest hash** covers the stable workflow ID and workflow kind,
  opaque source IDs, source classifications, linked person IDs, and normalized
  operation classes. A change to any of those rights-relevant facts creates a
  new material context that must be reviewed.
- The **graph hash** is audit and sync metadata for the whole execution graph.
  Moving a node, changing a sampler, or making another unrelated graph edit may
  change this hash, but does not by itself invalidate the rights manifest.

Human-readable source labels are not part of the rights hash. Raw filenames,
paths, prompts, node IDs, and graph JSON never enter the rights manifest.

## Privacy and network boundary

The local scan does not upload:

- raw workflow JSON;
- prompts;
- node IDs;
- local filenames or source paths;
- reference images;
- LoRA/model files;
- character sheets, storyboards, renders, or videos.

The connected flow sends only the information required for the canonical
workspace record:

- account, workspace, and project metadata entered by the user;
- person names, project roles, and contact/representative fields entered by the
  user;
- random workflow and source references, plus a SHA-256 fingerprint of the
  canonical API graph used only for audit/sync comparison;
- source kind, classification, linked person IDs, and normalized operation
  class names;
- the structured intended-use form;
- confirmation recipient, message, delivery selection, and stable request ID.

The pairing label is the generic `ComfyUI plugin`; the local hostname is not
sent. The frontend uses the system font stack and does not request Google Fonts.
The plugin token and private workflow/source mappings are stored in the plugin
data directory. Treat that directory as sensitive local application state.

**Disconnect** removes the local token only after server-side revocation is
confirmed, or after a `401` proves the token is already unusable. If Pluribus is
offline, the token stays local so revocation can be retried.

## Limitations

- This is graph-provenance detection, not face recognition or biometric
  identification.
- A synthetic-only result means no supported real-person source was found in
  the graph. It is not a legal conclusion.
- Pluribus does not upload, host, or inspect creative media through this plugin.
- The plugin does not create storyboards, character sheets, shots, or final
  renders.
- v0.4 does not provide contracts, e-signature, payment, union clearance,
  marketplace matching, or legal advice.
- Compensation and exclusivity can be included in the confirmation or
  explicitly marked as handled elsewhere; the plugin does not execute either.
- The plugin does not currently expose the canonical internal-review action.
- The setup dialog creates/selects a personal workspace; it does not yet list
  organization workspaces to which the user has been invited.
- Detection and AI-action mappings cover named node classes. New or custom
  nodes require explicit support or manual review.
- The panel has no manual normalized AI-action override yet; marker-only graphs
  do not verify downstream action inference or a render-ready production path.
- Link-only retry identity does not yet survive a request-dialog reload.
- Production v0.4 still requires a public-tag, deployed-API, new-user
  end-to-end rehearsal before launch certification.

## Compatibility with v0.3

The old local roster, invite, replacement, packet, and fixture-backed scanner
modules remain in the repository for compatibility tests and historical local
records. The production v0.4 panel does not load the seed roster and does not
use **Invite for terms** as its canonical path.

Existing plugin tokens carrying the legacy `plugin` scope continue to work as
a compatibility wildcard. Newly issued tokens use the narrower
`workspace:read`, `workspace:write`, and `confirmations:send` scopes.

Old local invite JSON is not silently promoted into canonical project people,
permission scopes, or confirmations. Keep it as historical local evidence and
create a reviewed v0.4 project record when continuing real work.

## Development

For plugin development, a symlink avoids repeated clones:

```bash
ln -s "$(pwd)" /path/to/ComfyUI/custom_nodes/comfyui-pluribus
```

The frontend is vanilla ES modules under `web/` and has no build step.

```bash
python -m pytest -v
node --test web/tests/contracts.test.mjs
```

The hosted Pluribus server/API and its database migrations are maintained and
deployed separately from this public plugin package. No server credential,
database migration, or private application source is included here.

## License

GPL-3.0-only — see [LICENSE](LICENSE). Server-side Pluribus services, canonical
workspace APIs, hosted confirmation workflows, and Pluribus trademarks are not
licensed by the plugin license; see [NOTICE](NOTICE).

More at [trypluribus.com](https://trypluribus.com/?src=comfyui).
