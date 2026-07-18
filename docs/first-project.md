# Your first project in Pluribus for ComfyUI

This guide follows the normal path for a producer making an AI-assisted ad
with real people in it. It starts from a clean install and a new Pluribus
workspace. It does not use the bundled test personas or pretend that a fixture
person belongs to the user.

> **Verification note:** on 2026-07-13, this producer/recipient loop ran against
> the deployed production API using public prereleases. The zero-state portion
> began on RC1. After RC1 exposed a false-offline timeout, the exact public
> `v0.4.0-rc.2` tag at commit
> `096571bc16b0d03f62dc469a0b0e6e68057651bb` replaced it in the same isolated
> state, recovered the work, and completed the remaining checks. The run used
> `alhassan@berkeley.edu`, a returning authenticated account that created its
> first v0.4 workspace, and a real Pluribus project with no demo personas. The
> exact coverage and remaining gaps are recorded below.

Pluribus helps you answer five practical questions:

1. Which real people may be represented by the sources in this graph?
2. Which project people are those sources linked to?
3. What exactly are we asking to do with their likeness, voice, or performance?
4. What did each recipient say about that request?
5. Has our own team separately reviewed the result?

It does not identify a face from pixels, clear rights, or replace your legal,
business-affairs, union, or production review.

## Before you start

Have the following ready:

- ComfyUI with Python 3.10 or newer;
- the character-sheet, storyboard, production, or final workflow you are
  already building;
- the real client/company name and a plain-language project description;
- the names and project roles of every real person whose likeness, voice, or
  performance source appears in the graph;
- a direct email or representative contact for each confirmation you expect to
  request;
- your actual proposed deliverables, channels, territory, dates, product
  category, restrictions, compensation handling, exclusivity handling, and
  final-approval requirement.

For an internal rehearsal, use fictional cast names and addresses you own. Do
not use a famous person, a bundled demo persona, or real biometric/confidential
campaign material merely to make the test look realistic.

## 1. Install from a clean plugin directory

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/trypluribus/comfyui-pluribus
```

`v0.4.0-rc.2` is the current production-rehearsed candidate. Pin it explicitly
if you are testing before the final release:

```bash
git clone --branch v0.4.0-rc.2 --depth 1 \
  https://github.com/trypluribus/comfyui-pluribus
```

Once the final release exists, use the stable tag for a long-lived install:

```bash
git clone --branch v0.4.0 --depth 1 \
  https://github.com/trypluribus/comfyui-pluribus
```

Restart ComfyUI and open **Pluribus** in the sidebar. A clean production
install should show no demo talent and no pre-existing permission state.

If the plugin directory is read-only, set a private writable data directory
before starting ComfyUI:

```bash
export PLURIBUS_DATA_DIR="$HOME/.local/share/comfyui-pluribus"
```

Use only one ComfyUI process with that directory at a time.

## 2. Find the person-bearing sources

Open the graph you want to review and choose **Find people**. The scan runs on
the ComfyUI server and follows supported graph provenance.

It can currently find:

- identity LoRAs loaded by `LoraLoader` or `LoraLoaderModelOnly`;
- image references, including references upstream of supported face-adapter or
  image-editing nodes;
- person-like prompts in `CLIPTextEncode` when no stronger source is present;
- explicit `Pluribus Source Marker` nodes.

Use a **Pluribus Source Marker** if the graph does not make a human source
explicit. Give the marker a stable local source key, a useful display name, and
an internal note. Those local fields help you read the graph; they are not
uploaded as source identity.

Current ComfyUI builds open a small **Value** editor when you select the source
key or display-name row. Enter the value and choose **OK** before moving to the
next field. Save the workflow after editing the marker.

Choose the node-library result once; in current ComfyUI builds, a double-click
can insert two marker nodes. Reference, LoRA, and unknown markers require a
source key. A prompt-only marker may omit the key, but it must include a display
name or note. Pluribus ignores incomplete markers and reports them above the
source list instead of treating them as people.

Review every result. The scan can miss a person behind an unsupported custom
node. It can also flag an image input without knowing whether the pixels truly
contain a person. It never performs face recognition.

At this stage, finding a source creates no person, permission, or confirmation
record in Pluribus.

## 3. Connect your Pluribus account

Choose **Connect**. The panel displays a short code and directs you to
[trypluribus.com/pair](https://trypluribus.com/pair).

1. Enter your email address.
2. Open the sign-in link on the same device. Check Spam if it does not arrive in
   Inbox. The production rehearsal verified that a Pluribus confirmation
   notification reached the Berkeley Inbox, but it reused an existing auth
   session; fresh signup/auth placement was not retested.
3. Enter the code shown in your own ComfyUI panel.
4. Approve the connection.

The plugin stores a device-specific token in its private data directory.
Pairing does not upload the graph, create a project, or copy people out of your
workflow.

If you previously disconnected this ComfyUI installation, pairing the same
owner account again should restore its existing personal workspace and projects
automatically. It should not ask you to create a duplicate personal workspace.

## 4. Set up the workspace explicitly on first use

On first use when the account has no owned personal workspace, choose **Set
up** and name your workspace. Self-serve v0.4 setup creates an individual
production workspace. The setup dialog does not yet list organization
workspaces to which you have been invited. If your company already has one, do
not create a duplicate and pretend it is the team workspace; use the authorized
workspace-selection path outside this dialog or ask the Pluribus operator to
bind the correct workspace.

This explicit setup step matters: an authenticated email address alone does
not silently create a company or claim that the user represents one.

## 5. Create the real project

Choose **New project** and record:

- **Project name:** the name your production team uses;
- **Brand / client:** the real company for which the creative is being made;
- **Agency:** optional;
- **Project context:** a concise explanation of the ad or campaign.

For example, a useful description is:

> Thirty-second paid and organic social video featuring two employees using
> the product in a workplace setting. Character sheet and storyboard precede
> the production and final graphs.

Do not put contract text or secrets in this short project description.

Select the kind that describes the current graph:

- **Character sheet** — identity and visual-consistency exploration;
- **Storyboard** — planned shots and sequences;
- **Production** — the graph used to make the creative;
- **Final** — final creative or deliverable review;
- **Other** — a rights-relevant graph that does not fit the four stages.

The plugin associates the local workflow with the project only after you make
this choice. A project may contain several workflow stages. A workflow may
belong to only one project at a time.

The first Pluribus scan inserts a private stable workflow key into ComfyUI graph
metadata, which marks the graph as changed. Save the workflow again after its
first scan/project binding. That save is what makes file-level recovery survive
a closed browser or later ComfyUI restart.

When you switch workflow tabs, Pluribus deliberately clears the previous
graph's actionable state. Choose **Find people** for the newly active workflow.
Each workflow must acknowledge the project's current use against its own exact
manifest; a storyboard response never silently approves a character sheet.

## 6. Link each source to the right people

Return to **Sources**, open the first detected card, and choose **Link people**.

For an existing project person, select their checkbox. For a new person, enter:

- full name;
- role in this project;
- optional direct talent email;
- optional representative type, name, and email.

Then save the link.

Repeat until each detected source is deliberately classified:

- **Linked** — connected to one or more project people;
- **Not a person** — reviewed and intentionally excluded from the people path;
- **Review required** — unresolved and still needs a human decision.

The relationship is many-to-many. A group image can link to several people,
and one person can link to a headshot, an identity LoRA, and a storyboard
reference. Do not duplicate the same person merely because they appear in
several sources.

Linking records who the source is meant to represent. It does not prove that
the person agreed, that the representative has authority, or that the proposed
use is permitted.

## 7. Complete the intended-use brief

Open **Intended use**. Fill out the form as if the recipient will read it
without access to your ComfyUI canvas.

### Project-wide use

Record:

- **Intended use:** a plain-language description of what will happen;
- **Deliverables:** each output being requested;
- **Channels:** social, broadcast, web, out-of-home, internal, or another
  channel;
- **Platforms:** named services when they matter;
- **Organic media / Paid media:** select each one actually included;
- **Territory:** every geography in scope;
- **Start / End:** the requested usage window;
- **Languages:** original and localized languages;
- **Product category:** what the person will be shown advertising;
- **Final creative approval required:** whether the person must approve the
  finished creative.

### Per-person terms

For each project person, review the values loaded from their canonical record:

- **Restrictions / prohibited contexts:** conflicts, sensitivities, or other
  limits that apply to this person;
- **Compensation for this person:** their specific compensation note, which is
  separate from the project-wide compensation-handling choice;
- **Usage comfort / caveats:** conditions or preferences that should travel
  with the request;
- **Representative authority notes:** the currently recorded basis or limit of
  representative authority. This is not an authority determination.

People can have different values. The plugin renders and saves each person
separately instead of copying one global restriction or blank compensation
value over every record.

### Compensation and exclusivity

For both compensation and exclusivity, choose one of three honest treatments:

- **Included in this request** — provide the actual summary the recipient is
  being asked to confirm;
- **Handled separately** — state that another process owns it and optionally
  summarize that process;
- **Not part of this request** — make the exclusion explicit.

Do not leave either topic ambiguous merely because the plugin does not execute
payments or contracts.

### Revocation and takedown

Record usable instructions for a revocation or takedown request, an expected
response time, and whether revocation requires disabling a person-specific
model/adapter or removing published creative from named platforms. Paid media
or external activation cannot be saved without an actionable path: instructions,
model disablement, or platform removal. A takedown SLA by itself does not say
who should act or what they should do.

### AI actions

Pluribus derives structured AI-action suggestions from supported downstream
operation classes. Current mappings include:

- face swap or supported image-editing nodes → face editing;
- face adapters → face-conditioned generation;
- identity LoRA loading → digital-replica generation use, not an assertion
  that this workflow trained the LoRA;
- reference image loading → biometric/reference-input processing;
- supported image-to-video → synthetic-performance rendering;
- person-like prompt paths → directed depiction generation.

Review these suggestions against what the graph really does. Unsupported
custom nodes and work performed outside this graph are not automatically
captured.

Choose **Save intended use**. Pluribus stores the result as a versioned
permission scope. The values are shared by the project, but the current
workflow records a separate acknowledgement of the exact scope version and
rights manifest. Repeat the review/save step for another workflow even when no
project-wide field changed.

Usage start and end are calendar dates, not midnight instants. The recipient
page must display the same entered dates in every time zone; the local launch
rehearsal found and fixed a one-day Chicago offset before release.

## 8. Preview and request confirmation

Open **People**. Each row shows three separate states: person/source link,
recipient request, and internal review.

For the first person, choose **Request confirmation** and review the preview.
It should show the project, client, person, intended use, deliverables,
channels, platforms, territory, languages, category, term, paid/organic
treatment, inferred AI actions, final approval, compensation and exclusivity
handling, that person's restrictions/compensation/caveats/authority notes, and
the revocation/takedown controls.

Enter:

- recipient email;
- recipient name;
- the role you expect them to hold;
- an optional message;
- **Send email** or **Copy secure link**.

The expected role is not an authority determination. The recipient must state
their own role and authority on the secure page.

If the server reports that delivery is pending or ambiguous, do not create a
second request. Retry or reconcile the same client request ID. A lost network
response may occur after the canonical request or provider email attempt was
already created.

**Retry same request** preserves that ID while the request dialog remains open.
Do not reload or close the dialog during an ambiguous link-only create. The
current v0.4 candidate cannot reconstruct the dialog-held retry ID after a
reload, and an idempotent replay may intentionally refuse to reveal the secure
URL again. If the dialog is lost, refresh canonical project state and reconcile
the existing request instead of creating another one blindly.

## 9. Rehearse the recipient experience separately

For an internal QA run, open the secure link as the owned test recipient—not in
the producer session.

The page should show the exact project and scope snapshot and ask the recipient
to identify their role and authority basis. Each person can receive one of the
following responses:

- Confirm;
- Confirm with caveat;
- Request changes;
- Decline;
- Not my authority;
- Exclude from this project.

The recipient can add notes and caveats. Their decision is evidence about the
presented request. It is not a signature on an unseen contract, a general
release for unrelated work, or an internal production approval.

## 10. Return to ComfyUI and read the response

Reload the project context or rescan. The person row should show the recipient
state without changing the internal-review axis.

Examples:

| Recipient event | Request axis | Internal-review axis |
|---|---|---|
| No request yet | Ready to request | Unchanged |
| Request created | Pending / Sent | Unchanged |
| Recipient confirms | Confirmed | Unchanged |
| Recipient adds a caveat | Confirmed with caveat | Unchanged |
| Recipient asks for edits | Changes requested | Unchanged |
| Recipient lacks authority | No authority | Unchanged |
| Recipient declines | Declined | Unchanged |
| Recipient excludes the person | Excluded | Unchanged |

The plugin never turns a recipient response into **Cleared**. In v0.4, the
internal review action remains in the canonical Pluribus workspace rather than
the ComfyUI panel. The panel reads the current-scope canonical state as **Not
reviewed**, **Pending**, **Approved**, **Changes requested**, **Blocked**, or
**Not required**. A recipient response does not change it.

## 11. Understand what makes a response stale

Pluribus calculates a rights manifest from:

- the random stable workflow reference;
- the workflow kind;
- random stable source references;
- each source's linked/not-person/review-required classification;
- linked project-person IDs;
- normalized downstream operation class names.

Changing one of those facts changes the rights manifest and requires a new
review of the intended use. For example, adding a person to a group source or
adding image-to-video use is material.

Project-wide intended use has its own version. Changing dates, deliverables,
channels, platforms, media treatment, category, terms, compensation or
exclusivity handling, person-specific terms, final approval, or revocation
controls makes an older response display **Scope changed — request again** even
when the graph manifest is unchanged. The old response remains historical
evidence; a new exact request and response restore current status.

The full graph hash is separate audit metadata. Moving nodes, changing a
sampler, or editing unrelated plumbing can change that graph hash without
changing the rights manifest. v0.4 does not invalidate a recipient response
merely because the whole graph changed.

This boundary was exercised in production with the public release candidate.
Editing an unrelated storyboard marker note preserved the current confirmation.
Changing another source from **Review required** to **Not a person** changed the
rights manifest and made the prior response display **Scope changed — request
again**.

Source display labels are excluded from the rights hash. Raw graph JSON,
prompts, node IDs, local source keys, and file paths are never part of the
remote manifest.

## 12. Restart and disconnect safely

Restart ComfyUI with the same `PLURIBUS_DATA_DIR`, reopen the workflow, and
verify that its project and opaque source bindings are recovered.

Keep ComfyUI bound to loopback or protect the whole server with trusted
authentication. The plugin's same-origin local routes act with the paired
device token, so an unauthenticated LAN/public ComfyUI deployment would expose
project reads and plugin mutations to other clients that can reach it.

When the connection is no longer needed, choose **Disconnect**. Wait for the
server to confirm revocation. If the service is offline, the plugin keeps the
local token so revocation can be retried rather than pretending it succeeded.
After successful revocation, the credential is removed but private
`bindings.json` is intentionally retained. Reconnecting should recover the same
local workflow/source identities, not mint unrelated ones. The production
rehearsal verified restart persistence, revocation of all issued credentials,
and returning-owner recovery after deployed server commit `94e3840`; the
installed plugin remained the exact public `v0.4.0-rc.2` tag throughout that
retest.

## What leaves your machine

The following stays local:

- graph JSON and graph filenames;
- prompts and node IDs;
- local source keys, file names, and paths;
- images, models, LoRAs, character sheets, storyboards, renders, and video;
- the private mapping between local keys and random Pluribus references.

The connected flow sends:

- the workspace and project fields you enter;
- the people and contact fields you enter;
- random workflow/source references, workflow kind, source kind,
  classification, linked person IDs, and operation class names;
- a SHA-256 fingerprint of the canonical API graph for audit/sync comparison;
- the intended-use form;
- confirmation recipient, expected role, note, delivery choice, and stable
  request ID.

The device label sent during pairing is `ComfyUI plugin`, not your hostname.

## Current product limits

- No face recognition or pixel inspection.
- No automatic identification of the person in an image.
- No media, graph, prompt, or model upload through this plugin.
- No storyboard, character-sheet, or render generation.
- No contracts, e-signature, payments, marketplace, or legal advice.
- No self-serve organization/team creation and no organization picker in the
  setup dialog; personal setup is individual and team access remains
  invitation-controlled.
- No internal approval action in the ComfyUI panel yet.
- Named node-class coverage only; custom nodes need manual review.
- There is no manual normalized AI-action override in the panel yet. A
  marker-only character sheet or storyboard can validate source/person and
  scope workflow, but it does not validate downstream action inference or a
  render-ready production graph.
- Link-only ambiguous retry identity is held by the open dialog and is not yet
  recoverable after a reload.
- The production-rehearsed candidate used marker-only workflows. It did not
  validate downstream AI-action inference, a render-ready production graph, or
  real media upload/generation.

## Production rehearsal evidence

On 2026-07-13, the rehearsal started from an anonymous public RC1 clone in a
clean ComfyUI base and data directory. The returning auth user
`alhassan@berkeley.edu` explicitly created their first v0.4 personal workspace
and a real project for Pluribus. No bundled talent or demo permission state was
present. When RC1 exposed the timeout described below, it was preserved and
replaced in that same isolated state by an anonymous clone of public
`v0.4.0-rc.2` at commit
`096571bc16b0d03f62dc469a0b0e6e68057651bb`. RC2 recovered the existing work
and completed the remaining production flow; this was not a second zero-state
run performed entirely on RC2.

The run bound separate character-sheet and storyboard workflows to that
project, created and reused the real project person, linked one person to
multiple sources, saved and reloaded the full intended-use brief, completed a
copy-link confirmation with a caveat, and completed an emailed approval. Both
recipient results synced while internal review stayed **Not reviewed**; v0.4
does not expose an internal approval action in the ComfyUI panel. Restart
recovered the private workflow bindings, and disconnect revoked the production
token and removed the local connection record.

Re-pairing initially exposed a returning-user defect: the new token did not
restore the owner's existing workspace. Server commit `94e3840`, deployed as
`dpl_ASu7WVj3vvEjen5ad1nAyjQFdJN8`, fixed that path. A fresh re-pair then
recovered the existing workspace, project, storyboard, links, and statuses
automatically with the exact `v0.4.0-rc.2` plugin still installed. An unrelated
storyboard marker-note edit advanced graph audit state but preserved the rights
manifest and **Confirmed** status; changing the crowd source from **Review
required** to **Not a person** changed the rights manifest and the storyboard
recipient status to **Scope changed — request again**. The separate
character-sheet response remained current for its unchanged manifest. A full
process restart preserved
the new disposition and recovered state. The final disconnect removed the local
connection secret, retained private bindings, and left all issued credentials
revoked. The reconnect correction was server-only; no RC2 plugin code changed.

The workflows used explicit source markers. No real image, model, character
sheet, storyboard media, render, or video was uploaded or generated, so the run
did not validate downstream AI-action inference or a render-ready production
path. The first release candidate incorrectly reported the production service
offline when a valid intended-use write exceeded its 10-second HTTP timeout;
`v0.4.0-rc.2` raised the bounded request timeout to 30 seconds, and a subsequent
equivalent intended-use write on the storyboard workflow completed successfully
in 13.59 seconds. It was not a verbatim replay of the earlier character-sheet
request.

## Public-launch rehearsal checklist

Run this from a clean public release, not a monorepo symlink:

- [x] A clean install shows no bundled people or fixture permission states.
- [x] A clean returning account pairs successfully.
- [x] The account explicitly creates or selects the correct workspace.
- [x] A real-client project is created with no demo talent.
- [x] Character-sheet and storyboard workflows bind to the same project.
- [x] A never-before-seen source can create and link a new project person.
- [ ] One source links to two people.
- [x] One person links to two sources.
- [x] Not-person and review-required classifications persist across restart.
- [x] The full intended-use form saves and reloads accurately.
- [x] Copy-link and email recipient paths both complete; the email notification
  reached the owned Berkeley Inbox.
- [x] The tested caveat and approval outcomes sync without changing internal
  review.
- [ ] The remaining recipient outcomes sync without changing internal review.
- [x] A rights-relevant source-disposition edit changes the manifest and makes
  the prior response display **Scope changed — request again**.
- [x] An unrelated marker-note edit changes only graph audit state and preserves
  the current confirmation.
- [ ] Network inspection confirms no graph, prompt, path, image, or model data
  leaves ComfyUI.
- [x] Restart recovers the project and private workflow/source bindings.
- [x] Disconnect revokes the token and removes the local connection record.
- [ ] Fresh signup/auth email placement is retested; the auth message available
  during this run was older and generic.
- [ ] Confirmation and signup/auth email placement is checked in a new owned
  non-Google mailbox.
- [ ] The internal-review handoff is completed in the canonical web workspace.
- [ ] A supported non-marker workflow exercises a normalized AI action plus
  production and final-review kinds, with network/privacy inspection.
- [ ] Slow writes and recipient submission have clear progress and safe
  retry/reconciliation behavior.
- [ ] The entire zero-state journey is repeated using only the exact final
  artifact.

The checked items passed across the deployed production rehearsal, which began
on public RC1 and ended on the exact public `v0.4.0-rc.2` tag. Leave the
unchecked items visible; do not infer them from automated allow-list tests or
the marker-only rehearsal. Fresh signup/email placement, internal review, a
privacy-inspected non-marker production/final flow, latency/reconciliation, and
the exact-final-artifact zero-state run are hard final-tag gates. The one-
source-to-two-people and remaining-recipient-outcome items are additional
production coverage; if still open at tagging, documentation must call them
automated-only or unverified rather than production-certified. The stable
`v0.4.0` tag remains on hold while the hard gates are open.
