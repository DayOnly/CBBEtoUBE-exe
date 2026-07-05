# Whole-Converter Audit — 2026-07-04 (execution log)

Executed against the plan `CONVERTER_AUDIT_PLAN_2026-07-04.md`. Baseline WIP
snapshot committed as `0fd160b` (branch `testing`), diff-ref `a730dad`.
Suite before: 552. Suite after: **565** (+13 regression tests). Golden mesh A/B
(7 synthetic default-flag cases) **byte-identical** pre/post.

Five review clusters (one agent-pass each) + adversarial code-read verify of
every CONFIRMED finding. No third-party mod names used.

## Fixes landed (CONFIRMED, each with a regression test)

| ID | File | Fix |
|---|---|---|
| C4-F1 | gui.py `_refresh_mods` | **Regression**: worker thread was never started -> "Refresh mod list" dead, selected-mods armor workflow unusable. Restored the `.start()`. |
| C4-F2 | gui.py `_ov_refresh` | **Regression**: two identical `.start()` (the mis-pasted one from F1) ran the overlay scan twice + double-populated. Removed the dup. |
| C4-F3 | gui.py `_apply_theme` | `state["_canvases"]` grew unbounded across dialog opens; theme switch did O(dead) work. Prune via `winfo_exists()`. |
| C4-F4 | gui.py `_diag_done`/`_finish`/`_poll` | Cross-thread `after`/poll callbacks touched root widgets with no exists-guard -> TclError if app quit mid-work. Early-return on `not root.winfo_exists()`. |
| C2-F1 (seeded #5) | auto_convert.py | Frozen `--incremental` floor collapsed to 0.0 (onedir has no loose `src/*.py`) -> redeployed exe silently reused stale meshes. Extracted `_incremental_code_mtime()`; stat the exe when frozen. |
| C2-F3 (seeded #8) | auto_convert.py `_find_armor_mod_dirs` | Memo key omitted `CBBE2UBE_NO_VANILLA_SWEEP`, which changes the candidate set -> stale list on toggle. Added flag to key. |
| C2-F5 | auto_convert.py | espgen sidecar JSON written non-atomically -> torn snapshot breaks later `--plugins-only`. Routed through `atomic_write_bytes`. |
| C1-F2 | auto_convert.py `_fsp_linked_abs` | One malformed INI line wiped the WHOLE full-SP exclude set -> mod-wide double-cover. Per-line try/continue. |
| C3-F1 (seeded #3) | nif_convert.py `_strip_jiggle_weights_map` | Unguarded Pelvis fallback on a Pelvis-less jiggle-only vert -> no-STB origin spike (floor streak). Mirrored the `_strip_genital_weights_map` `has_pelvis` guard. |
| C3-F3 (seeded #4) | nif_convert.py re-author path | Hand-rolled `save()`+`os.replace` bypassed the shared atomic saver. Routed through `atomic_nif_save`. |
| C5-F1 | bsa_strings.py `read_file` | Bounds check gated on `not self._eager` -> eager (default) path OOB-read on a crafted offset. Made the check unconditional. |
| C5-F2 | bsa_strings.py `parse_strings_table` | File-supplied `count` unbounded -> DoS spin / raw struct.error. Clamped to `(len-8)//8`. |
| C5-F3 | bsa_strings.py `_parse` | Header `folder_count`/per-folder count trusted; negative `blk_off` index-wrap. Reject absurd counts (clean ValueError); clamp per-folder; skip out-of-range block offsets. |

## Seeded items — disposition

- #1 `_NifPool` bypass — **CLEAN**. Pool is a crash-isolation *process* pool in auto_convert; the one non-pool path is an intentional single-item fast path. No mesh-side leak.
- #2 `plugin_file_index` order — **CLEAN**. `build_armo_winner_index` + merge rebase deterministic; ESM-tier (0x201) stable sort correct.
- #3 PELVIS STB spike — **FIXED** (C3-F1).
- #4 `save_nif` non-atomic — **substantially CLEAN**; main path already atomic. Lone re-author writer **FIXED** (C3-F3).
- #5 `--incremental` frozen floor — **FIXED** (C2-F1).
- #6 sweep orphan ARMAs — fatal form **REFUTED** (ESL cap counts records, not links, so no overflow). Residual = **C1-F1 orphan-mint bloat: CONFIRMED, non-fatal, DEFERRED** (see below).
- #7 coverage over-mints hands/feet — **PLAUSIBLE, DEFERRED** (couples with C1-F1; armature-level dedup lives in ube_patcher).
- #8 `_ARMOR_MOD_DIRS_CACHE` key — **FIXED** (C2-F3).
- #9 GUI `trace_add` in `_populate` — **CLEAN**. Registered once per dialog open; fresh StringVar per open; dies with the Toplevel.
- #10 removal manifest — **out of scope** (gated on in-game round 3); not touched.

## DEFERRED — CONFIRMED but fix mutates emitted ESP records; needs a real
multi-source sample to A/B (none in this environment). Shipping a blind
record-dropping change is riskier than the non-fatal bloat it fixes.

- **C1-F1 / seeded #6** (ube_patcher merge): when two source patches in
  different split pieces mint an ARMA for the SAME `(armo, src-armature)`, the
  full-SP link pass dedups the LINK (seen_pairs) but both ARMA *records* still
  ship -> orphan records waste ESL slots (can force a needless split/downgrade).
  NON-FATAL: cap accounting counts records, so no FormID overflow/CTD. Requires
  a genuine multi-mod overlap (two ESPs overriding one master ARMO across split
  pieces).
  Fix design: after the link pass builds `sp_by_armo`, compute
  `kept = linked-fids UNION fids-referenced-by-deduped_armo-payloads`; drop
  own-minted ARMA records not in `kept` BEFORE `out_esp.save` (move link-fid
  collection above the save at ~4291); re-derive `own_arma_count`. Guard: must
  NOT drop ARMAs referenced by surviving ARMO overrides (mixed mode). Validate
  by comparing record/link counts on real full-SP output.
- **C2-F4 / seeded #7** (armature-level dedup) — couples with C1-F1.
- **C2-F2** (`refresh_mod_esp` uses per-source master dirs for the vanilla-sweep
  source in `--plugins-only`, diverging from the full run) — needs a real
  `--plugins-only` run to A/B; fix = thread `batch_master_data_dirs` for the
  sweep source.

## Lower-severity documented (not fixed)

- C1-F3 (missing Combined INI silently empties exclude set) — an empty set is
  legitimately correct when full-SP is off, so a hard error would regress the
  common path. C1-F2's per-line hardening covers the malformed-line case.
- C1-F4 (filterByArmors plugin-name case differs across emit sites) — cosmetic;
  internal exclude comparison is already consistently lowercased.
- C5-F4 (other sidecar JSON writers non-atomic) — acceptable per the atomicity
  rule (converter-internal state, every reader tolerates a torn file).

## atomic_io.py — verified CORRECT on Windows
File fsync yes; dir fsync correctly absent (can't fsync a dir handle on Windows;
NTFS + MoveFileEx orders durably); same-volume mkstemp->os.replace atomic; temp
cleanup on BaseException; locked dst -> OutputLockedError. No defect.
