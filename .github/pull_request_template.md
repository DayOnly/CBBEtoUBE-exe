<!-- Keep this short. Delete any section that does not apply.
     Base branch: testing. main receives testing through release PRs only. -->

## What this changes

<!-- One or two sentences. The *why* matters more than the *what* — the diff
     already shows the what. -->

## How it was verified

- [ ] `python -m pytest -q` passes locally
- [ ] New behaviour has a test, or the change is not testable in isolation (say which)
- [ ] Verified in game <!-- say which armor / modlist, or delete this line -->

## Release checklist

<!-- Only for changes that ship. Delete otherwise. -->

- [ ] `src/version.py` bumped
- [ ] `dist/CBBEtoUBE/` rebuilt (`pyinstaller CBBEtoUBE.spec` or `scripts\build_exe.ps1`)
- [ ] `python scripts/release_gate.py stamp-check HEAD` passes on the commit you will tag — rebuild after any amend or rebase, or "in history" fails
- [ ] Once the release is published, `python scripts/release_gate.py stamp-check vX.Y --asset <the uploaded zip>` passes (the release-gate workflow runs it too)
- [ ] README / docs/DESIGN.md updated if behaviour or dependencies changed

## Notes for review

<!-- Anything non-obvious: a tradeoff you took, a case you deliberately did not
     handle, a follow-up you want tracked separately. -->
