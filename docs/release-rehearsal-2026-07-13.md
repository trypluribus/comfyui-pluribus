# v0.4.0-rc.2 production rehearsal — 2026-07-13

This dated record preserves the exact verification boundary for the supported
`v0.4.0-rc.2` release candidate. It is evidence and release-gate history, not
the evergreen onboarding guide. Start with [Your first project](first-project.md)
for normal use.

The rehearsal began from an anonymous public RC1 clone in a clean ComfyUI base
and data directory. An owned returning test account explicitly created its
first v0.4 personal workspace and a real project with no bundled talent or demo
permission state. After RC1 exposed a false-offline timeout, the exact public
`v0.4.0-rc.2` tag at commit
`096571bc16b0d03f62dc469a0b0e6e68057651bb` replaced it in the same isolated
state, recovered the work, and completed the remaining checks. The zero-state
journey was not repeated solely on RC2.

## Verified behavior

The run:

- bound separate character-sheet and storyboard workflows to the same project;
- created and reused a real project person with no demo personas;
- linked one person to multiple sources;
- saved and reloaded the full intended-use brief;
- completed a copy-link confirmation with a caveat and an emailed approval;
- synced both recipient results while internal review remained **Not reviewed**;
- preserved private workflow bindings across restart; and
- revoked the production token and removed the local connection record on
  disconnect.

Re-pairing initially exposed a returning-user defect: a new token did not
restore the owner's existing workspace. Deployed server commit `94e3840` fixed
that path. A fresh re-pair then recovered the existing workspace, project,
storyboard, links, and statuses with the exact RC2 plugin still installed.

An unrelated storyboard marker-note edit advanced graph audit state but
preserved the rights manifest and **Confirmed** status. Changing the crowd
source from **Review required** to **Not a person** changed the rights manifest
and made the storyboard response display **Scope changed — request again**. The
separate character-sheet response remained current for its unchanged manifest.
A full restart preserved the new disposition. The final disconnect removed the
local connection secret, retained private bindings, and left issued credentials
revoked. The reconnect correction was server-only; no RC2 plugin code changed.

Usage dates also remained calendar dates across time zones after a pre-release
fix for a one-day offset.

## Verification boundary

The workflows used explicit source markers. No real image, model, character
sheet, storyboard media, render, or video was uploaded or generated. The run
therefore did not validate downstream AI-action inference, a render-ready
production path, or the unreleased local identity-analysis feature now present
on `main`.

RC1 incorrectly reported the production service offline when a valid
intended-use write exceeded its 10-second HTTP timeout. RC2 raised the bounded
request timeout to 30 seconds. A subsequent equivalent intended-use write on
the storyboard workflow completed in 13.59 seconds; it was not a verbatim
replay of the earlier character-sheet request.

## Public-launch rehearsal checklist

Run this checklist from a clean public release, not a monorepo symlink:

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
- [x] Copy-link and email recipient paths both complete.
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
- [ ] Fresh signup/auth email placement is retested.
- [ ] Confirmation and signup/auth email placement is checked in a new owned
  non-Google mailbox.
- [ ] The internal-review handoff is completed in the canonical web workspace.
- [ ] A supported non-marker workflow exercises a normalized AI action plus
  production and final-review kinds, with network/privacy inspection.
- [ ] Slow writes and recipient submission have clear progress and safe
  retry/reconciliation behavior.
- [ ] The entire zero-state journey is repeated using only the exact final
  artifact.

The checked items passed across a rehearsal that began on public RC1 and ended
on the exact public RC2 tag. Do not infer unchecked results from automated
allow-list tests or the marker-only rehearsal.

Fresh signup/email placement, internal review, a privacy-inspected non-marker
production/final flow, latency/reconciliation, and an exact-final-artifact
zero-state run remain hard final-tag gates. One-source-to-two-people and the
remaining recipient outcomes are additional production coverage; if still open
at tagging, release documentation must call them automated-only or unverified.
The stable `v0.4.0` tag remains on hold.
