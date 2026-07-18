# Contributing to comfyui-pluribus

Thanks for your interest in improving the Pluribus ComfyUI plugin.

## Bug reports and small fixes

Issues, bug reports, and small documentation or typo fixes are welcome —
open an issue or a pull request directly.

## Code contributions

Substantial code contributions require a signed Contributor License
Agreement (CLA) before they can be merged. The CLA lets you keep your
copyright while granting Pluribus the rights needed to maintain,
sublicense, and relicense the project over time.

The CLA process is being finalized. Until it is in place, please open an
issue describing the change you'd like to make before writing significant
code, so we can coordinate.

## Maintainer sync boundary

Pluribus maintains the canonical plugin source under `comfyui-pluribus/` in
the private product monorepo and publishes its exact tested tree to this public
repository. External issues and pull requests are still welcome here. Before a
standalone contribution is merged, a maintainer will land or reconcile it in
the canonical subtree and verify that both repository trees match.

A merge to the standalone repository does not by itself create a versioned
release. Tags, GitHub releases, and Comfy Registry publication follow a
separate release decision and clean-install verification.

## Scope note

This repository contains the local ComfyUI plugin only. Pluribus
server-side services, roster and consent APIs, clearance workflows, and
the Pluribus name and marks are not part of this repository and are not
covered by its license.

## Development

```bash
python -m pip install -r requirements-test.txt
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
node --test web/tests/contracts.test.mjs
```

See `README.md` for installation and architecture notes.
