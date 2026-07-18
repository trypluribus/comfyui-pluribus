# Security policy

## Supported versions

| Version | Security support |
| --- | --- |
| `v0.4.0-rc.2` | Supported release candidate |
| `main` | Unreleased development; reports welcome |
| Earlier tags | Not supported |

The reserved `0.4.0` package metadata on `main` is not a release. A source-sync
merge, tag, GitHub release, and Comfy Registry publication are separate
decisions.

## Report a vulnerability privately

Email [security@trypluribus.com](mailto:security@trypluribus.com) or use
[GitHub private vulnerability reporting](https://github.com/trypluribus/comfyui-pluribus/security/advisories/new).
Include the affected tag or commit, ComfyUI and Python versions, operating
system, reproduction steps, and impact. If possible, include a minimal
reproducer that contains no private media.

Do not put device tokens, account links, email magic links, private campaign
material, biometric data, source media, local file paths, or unredacted logs in
a public issue. We will acknowledge a private report and coordinate the next
safe disclosure step before details are published.

## Security boundary

This plugin stores a device credential and private workflow/source bindings on
the machine running ComfyUI. Same-origin `/pluribus/*` routes can use that
credential to communicate with the paired Pluribus workspace. Exposing an
otherwise unauthenticated ComfyUI server to a LAN or the public internet also
exposes that local proxy surface. Reports about token storage, route access,
unexpected network transfer, local-media handling, or identity-analysis data
are in scope.
