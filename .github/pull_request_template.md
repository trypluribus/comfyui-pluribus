## Summary

<!-- What changed, and why? -->

## Related issue

<!-- Link the issue. Please coordinate substantial work before implementation. -->

## Verification

- [ ] `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider`
- [ ] `node --test web/tests/contracts.test.mjs`
- [ ] I tested any affected ComfyUI interaction manually, or explained why it was not needed.

## Privacy and network boundary

- [ ] The change does not add graph, prompt, model, path, source-media, biometric, or credential transfer; or I documented and tested the exact new transfer.
- [ ] Fixtures, logs, and screenshots contain no tokens, private media, campaign material, or personal information.

## Contribution and release boundary

- [ ] I read `CONTRIBUTING.md`, including the current CLA limitation for substantial external code.
- [ ] I understand that merging this pull request does not create a tag, GitHub release, or Comfy Registry publication.

Maintainers only:

- [ ] Reconciled with the canonical `comfyui-pluribus/` subtree.
- [ ] Verified the canonical and standalone trees match before public merge.
