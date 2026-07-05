# Whole-Converter Audit Plan — 2026-07-04

Planned on Fable 5; intended for execution in a follow-up session (Opus).
Prior audits: `ROBUSTNESS_AUDIT_2026-06-22.md` (robustness + security S1–S6)
and the 2026-06-24 five-agent whole-converter audit. This audit covers the
~5,900 changed lines since then (uncommitted on top of a730dad): the FULL
SkyPatcher pivot, vanilla sweep, espgen snapshots / --plugins-only, failure
popup, GUI overhauls, and every fix batch in between.

## Ground rules (from working style)

- Every finding must be **CONFIRMED with a concrete failure scenario**
  (inputs/state → wrong output) before it is fixed. PLAUSIBLE-only findings
  get a test that tries to reproduce; no fix without a repro or a proof.
- Anything touching mesh/ESP output at default flags needs a **byte-identical
  A/B** (convert a fixed sample before/after; hash outputs). Golden samples:
  the synthetic-NIF e2e harness + a small real-mod source.
- Suite must stay green after every fix batch (currently 552).
- No third-party mod/armor names in code or comments.
- Fixes land in `testing`; deploy (robocopy + /XF ×4) and **co_code
  bytecode-verify** the deployed exe at the end, not per-fix.

## Step 0 — snapshot (needs user OK)

38 uncommitted paths (11 M, 25 new, 2 D). Auditing a large uncommitted tree
risks losing the baseline mid-fix. **Recommend: commit the current state to
`testing` as a WIP snapshot first** ("full-SP pivot + vanilla sweep + GUI
hardening, pre-audit snapshot"). If declined, at minimum `git stash create`
a ref or copy the tree before edits begin.

## Step 1 — risk map (what to audit hardest)

Highest-churn / highest-blast-radius first:

| Zone | Why hot |
|---|---|
| A. Full-SP link pipeline (ube_patcher: link emission both sites, sidecars, merge dedup, INI write; auto_convert: #fsp-dedup exclusions, coverage passes) | New delivery path for ALL armor; a silent link bug = invisible armor everywhere. Two emission sites must stay in lockstep. |
| B. Vanilla sweep (this session): `_vanilla_sweep_esps`, masterless-ESM RNAM gates (BOTH copies), BSA index Data-dir append + prefixes, VFS union keys, separate-pass retry + claims rollback, preflight probe | Brand new; touches discovery, resolution, batch loop, GUI. The claims snapshot/rollback interacts with a SHARED mutable set. |
| C. Dual-path flag machinery (full-SP on/off, body-SP superseded, winner rebase, ARMO override builders slated for removal) | Pivot left two parallel delivery paths; flag-off is claimed byte-identical — verify it still is after a month of edits landing around it. |
| D. espgen snapshots + refresh_mod_esp ("mirrors auto_convert_mod's ESP tail, kept in sync") | Mirror code drifts. auto_convert_mod's tail changed several times since (sweep, failures). Diff the two paths line-by-line; consider extracting the shared tail. |
| E. Batch loop rework (separate sweep pass, serial retry, `_convert_one` closure, failure recorder, merge gate changes) | Control-flow surgery in the highest-traffic function; the retry path claims-rollback and the closure capturing loop vars deserve adversarial eyes. |
| F. GUI thread/lifecycle (progress callbacks via root.after from worker threads, `_cfg` guards, dialog rebuild-on-filter, trace_add registrations, popup file contract) | Every recent dialog got new cross-thread updates. Look for: after() on destroyed widgets, trace leaks, re-entrancy (double-click Convert/Refresh), state[] races. |
| G. Environment assumptions | usvfs (no Data-tree walks anywhere else? grep every rglob/iterdir for Data-dir reachability), windowed-exe launch contexts, long paths (>260), non-ASCII mod names, missing profile, Vortex-style layouts. |
| H. Parsers on untrusted input (esp.py bounds, tri.py, bsa_strings, hh_offset, sidecar/espgen/failures JSON read-back) | Prior audit hardened these; new READERS were added since (sidecar JSON, espgen JSON, INI re-parse in _cmd_auto `_fsp_linked_abs`, failures JSON in GUI). Same standards apply. |
| I. Atomicity / crash-safety of ALL game-loaded outputs | atomic_io exists; verify every NEW writer uses it (sidecars? espgen snapshots? failures file is GUI-only=ok). Carry-forward: `save_nif` non-atomic. |
| J. ESL / split invariants | Combined now splits into 3 pieces; master-order flag 0x201 rule, links pointing across pieces, dedup set shared across pieces, ESL cap accounting incl. sweep-minted ARMAs + orphan mints from Skyrim.esm+Update.esm double-processing. |

## Step 2 — seeded findings (carry-forwards + known deferrals)

Verify/close each explicitly; they are pre-approved audit targets:

1. `_NifPool` bypass (2026-06-24 OPEN).
2. `plugin_file_index` order (2026-06-24 OPEN).
3. PELVIS STB spike (2026-06-24 OPEN).
4. `save_nif` non-atomic (2026-06-24 OPEN; conflicts with atomic-IO rule).
5. `--incremental` frozen-exe floor collapse (latent; documented in deploy
   memory) — fix or remove the flag from the frozen build.
6. Sweep orphan ARMAs: Skyrim.esm + Update.esm both mint for overridden
   ARMAs → dedup keeps one LINK but both records may survive in Combined
   (bloat toward the ESL cap). Measure on the real output; fix if material.
7. Coverage fallback still mints UBE-PRIMARY hands/feet for variant items
   (720-set note from full-SP round 1) — decide: fix or document.
8. `_ARMOR_MOD_DIRS_CACHE` keys ignore the new `progress` kwarg (correct) but
   also ignore env flags that now change results (`CBBE2UBE_NO_VANILLA_SWEEP`
   affects union_all keys) — stale-cache-across-toggle bug class.
9. GUI `filter_var.trace_add` registered inside `_populate` — verify single
   registration per dialog open, no leak across reopens.
10. Removal manifest (ARMO-override machinery) — **explicitly OUT of this
    audit**; it executes only after in-game round-3 confirmation. But the
    audit should NOT invest in polishing code the manifest deletes; tag such
    findings "dies-with-removal" instead of fixing.

## Step 3 — execution shape (Opus session)

Five review clusters, one agent-pass each, then an adversarial verify pass on
every finding (refute-first), then one fix batch per cluster:

- **Cluster 1:** ube_patcher.py (4.6k) — zones A, C, J, H.
- **Cluster 2:** auto_convert.py (4.3k) — zones B, D, E, G, I.
- **Cluster 3:** nif_convert.py (11.4k) — highest LOC but least churned this
  month; scope to: diffs since a730dad only, plus seeded #3, plus any
  cross-module contract the other clusters implicate. NOT a full re-read.
- **Cluster 4:** gui.py + gui_settings + preflight + exclusions + paths +
  discovery (small files, heavy recent churn) — zones F, G.
- **Cluster 5:** parsers + IO: esp.py, tri.py, bsa_strings, atomic_io,
  hh_offset, nif_io/nif_patch + every JSON/INI read-write added since 06-24 —
  zones H, I.

Per cluster: findings ranked; CONFIRMED get fix + test in the same batch;
PLAUSIBLE get a repro attempt, then fix or documented dismissal. After all
clusters: full suite, byte-identical A/B on golden samples (flag-off AND
full-SP-on), rebuild, deploy, co_code-verify, update the audit log file
(`CONVERTER_AUDIT_2026-07-XX.md`) + memory topic.

## Step 4 — exit criteria

- All seeded items closed (fixed, disproven, or tagged dies-with-removal).
- Zero CONFIRMED findings left unfixed.
- Suite green (≥552; new tests for every fix).
- A/B: default-flag output byte-identical pre/post audit; full-SP output
  semantically identical (record counts, link counts, INI targets).
- Deployed exe bytecode-verified; audit log + memory updated.

## Explicit non-goals

- Removal manifest execution (separate, gated on in-game round 3).
- Performance work beyond confirming no regressions (speed levers live in
  their own topic).
- Overlay UV-transfer prototype (SANDBOX; not shipped).
- In-game validation (user-driven, separate).
