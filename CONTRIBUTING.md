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

## Scope note

This repository contains the local ComfyUI plugin only. Pluribus
server-side services, roster and consent APIs, clearance workflows, and
the Pluribus name and marks are not part of this repository and are not
covered by its license.

## Development

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
```

See `README.md` for installation and architecture notes.
