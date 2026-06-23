# CBBE-to-UBE robustness / failure-handling audit

Branch: `main` == `testing` @ `319f14f`
Date: 2026-06-22
Scope: failure-handling robustness (atomic writes, postflight validation, swallowed
failures, exit-code accounting, worker-pool death handling). NOT a feature/correctness
audit of the conversion math.

Method: five independent code traces (worker pool, swallowed failures, exit codes,
postflight completeness, atomic-write coverage). Every High/Medium item below was
re-read in source and confirmed; line numbers are against the commit above.

Legend: [ ] open  [~] in progress  [x] fixed (with commit)

---

## Overall posture

Detection is strong (atomic writes, a real merged-ESP postflight CTD gate, most
failures surfaced). The weakness is uniformly in *recovery and escalation*:
confirmed-broken-output conditions classified as warnings (exit 0), one transient
worker crash cascading to the whole batch, and a few fully-silent failure paths.

---

## Fix log - 2026-06-22 (suite 347 -> 358, all green)

FIXED this pass (no reconvert needed - source-only; exe rebuild needed to ship):
- H3  auto_convert.py - NEW `_NifPool`: a self-healing wrapper around the batch-shared process pool. A worker process death (native pynifly crash -> BrokenProcessPool) rebuilds the pool and re-runs the not-yet-done items in ISOLATION (one at a time), so only the true crasher is dropped - not the rest of the mod, and (shared pool persists) not every later mod. GIVE_UP_AFTER=5 consecutive crashes = systemic-failure backstop. Proven with a REAL subprocess crash-sim test (worker os._exit -> genuine broken pool -> recovery + pool survives).
- H1  auto_convert.py - per-NIF equip-CTD/zero-vert invariants now `overall_failures` (exit 2), symmetric with the merged-ESP gate.
- H2  auto_convert.py - `_cmd_auto` tracks `post_merge_failures`; an exception in any REQUIRED coverage phase now folds into the return code.
- H4  nif_convert.py - phase-2 HDT gate keys on a new `hdt_injected` flag (not `hdt_xml_path is None`); a found-but-failed source-XML attach now falls through to regen AND surfaces via result_reason.
- H5  auto_convert.py - `_warmup_worker` barrier.wait now has a 300s timeout (+ BrokenBarrierError swallow); a worker dying mid-warm-up can't wedge the survivors.
- M1  ube_patcher.py - unloadable/not-found masters in the UBE-race ARMO scan are collected and surfaced via `validation_warnings` (master-coverage-skipped), gated on master_data_dirs to avoid spam.
- M2  ube_patcher.py - `unmappable-master-ref` added to `_POSTFLIGHT_CTD_PREFIXES` (it documents itself as a startup crash; can't false-positive since the check only fires on a locatable master).
- M3  auto_convert.py - standalone `merge` subcommand now runs `postflight_validate_combined` on the merged output and returns 2 on a CTD-class finding.
- M4  esp.py - Record.parse/Group.parse raise clean ValueErrors (no -O-stripped asserts, no cryptic struct.error) + nested-GRUP zero-size guard (no infinite loop). Happy path byte-identical.
- M5  auto_convert.py - texture copy + both SkyPatcher coverage INIs routed through atomic_io (no truncated game-loaded files).
- M6  ube_patcher.py - alt-texture reconcile now surfaces converted NIFs that EXIST but fail to load (stale color-variant indices kept), distinct from a legitimately absent path.
- Tests: tests/test_robustness_audit_2026_06_22.py (11 new regression tests, incl. a real-subprocess crash-sim) + tests/_crash_worker.py helper.

STILL OPEN (see items below): L1-L4 (low/latent) only. All High + Medium findings fixed.

---

## High severity

### [x] H1 - Equip-CTD NIFs only warn; process exits 0  (FIXED)
`_nif_invariant_issues` (auto_convert.py:2043) flags over-cap single-partition shapes
as "split failed -> equip CTD risk" and zero-vert shapes as degenerate, on the final
reloaded bytes. Caller routes it to `overall_warnings` (auto_convert.py:1675); exit
code keys only off `overall_failures` (auto_convert.py:1911). A mesh that hard-CTDs on
equip ships exit 0. Asymmetric with the merged-ESP CTD path, which fails the build.
Fix: promote these two invariant classes to `overall_failures`.

### [x] H2 - The "REQUIRED" coverage ESPs cannot affect the exit code  (FIXED)
`_cmd_auto` fixes `rc = _cmd_convert(conv)` (auto_convert.py:2767), then runs vanilla
race-compat, vanilla bodies, mod non-body/body coverage, overlay, race-skin fold AFTER
that - each in a try/except that only prints "skipped" (e.g. 2799-2800), returning the
stale `rc` (2978). Plugins the closing message calls REQUIRED can fail to generate and
still exit 0.
Fix: accumulate post-merge phase failures and fold into the return code.

### [x] H3 - One native worker crash cascades to every remaining mod in the batch  (FIXED via _NifPool)
Single shared ProcessPoolExecutor (auto_convert.py:1495) threaded into every source
mod. A native pynifly crash marks the whole pool broken. Surfaced (not silent):
remaining futures drain to "worker process died" (`_drain_result`, 97-108) and later
mods hit the submit-guard (936-944). But no pool rebuild/retry exists -> all meshes for
mods #4..N go unconverted (invisible) after a crash in mod #3.
Fix (larger): detect BrokenProcessPool, rebuild the pool, resubmit remaining items.

### [x] H4 - Silent no-physics chain on the body-swap path  (FIXED)
nif_convert.py:9150 sets `hdt_xml_path` from source; injection at 9168-9175 is wrapped
in `except: pass` (9176-9177); regen fallback gated `if hdt_xml_path is None` (9256). A
source XML found + injection throwing leaves `hdt_xml_path` non-None -> regen skipped ->
saved NIF has no physics ref, nothing surfaced. Only backstop `_finalize_hdt_physics`
also swallows (9290-9291). Gate conflates "no source XML" with "injection failed".
Fix: track injection success separately; run regen if injection failed; surface it.

### [x] H5 - Warm-up barrier can deadlock with no timeout  (FIXED)
`_warmup_worker` ends with `barrier.wait()` (auto_convert.py:141), no timeout, sized to
num_workers. A worker dying during prewarm before the barrier wedges survivors forever;
`as_completed` never yields them; the call site catches raises, not hangs.
Fix: pass a timeout to barrier.wait() and handle BrokenBarrierError.

---

## Medium severity

### [x] M1 - Master not found/unloadable -> silent missing UBE-race coverage  (FIXED)
ube_patcher.py:1593-1594 and 1607-1610 skip the master-ARMO override scan without
recording to `master_scan_stats`. Armors defined in that master get no UBE-race ARMA
override (invisible on UBE actors), no signal.
Fix: record the skip into stats and surface it as a warning.

### [x] M2 - `unmappable-master-ref` documented as a crash but classified non-fatal  (FIXED)
Its comment (ube_patcher.py:2163) and emit text (2195) say "startup crash," but the
prefix is absent from `_POSTFLIGHT_CTD_PREFIXES` (1895-1898) -> postflight files it
`soft`/warn. Mitigated by the generation-time skip (1622), but the classification
contradicts its own stated severity.
Fix: decide - either add the prefix to the CTD set, or soften the comment/emit text to
match reality (it is avoided at generation, so warn may be correct). Resolve the
inconsistency.

### [x] M3 - Standalone `merge` subcommand: no postflight, always exit 0  (FIXED)
`_cmd_merge` (auto_convert.py:2992-3032) returns 0 unconditionally - never calls
`postflight_validate_combined`, never inspects `downgraded_to_full_esp`. The exact
artifact postflight guards is unvalidated on this path.
Fix: run postflight on the merged output; return non-zero on CTD-class findings.

### [x] M4 - Validators can crash on a malformed source ESP  (FIXED)
`validate_patch` uses `esp.ESP.load`; `Record.parse` has unguarded struct.unpack,
zlib.decompress, and `assert len(payload)==uncomp_size` (esp.py:115-126); `Group.parse`
asserts GRUP and skips nested groups by `inner += inner_size` with no zero/overshoot
guard (157,171-172). Hardened only at iter_subrecords. Per-source / vanilla-compat call
sites are unguarded or swallow.
Fix: harden Record/Group.parse (bounds-check, replace asserts with clean errors).

### [x] M5 - Non-atomic writes of game-loaded artifacts  (FIXED)
auto_convert.py: texture copy `shutil.copy2(f, out)` (994), and two SkyPatcher coverage
INIs via raw `write_text` (2874, 2925). `atomic_copy`/`atomic_write_bytes` exist and are
used elsewhere. Truncated DDS -> garbage texture; truncated INI -> partial silent
distribution.
Fix: route all three through atomic_io.

### [x] M6 - Alt-texture reconcile silently skips records whose converted NIF won't load  (FIXED)
ube_patcher.py:284-297 (`shapes_for` -> None on load failure), consumed 318-319, leaves
stale source 3D indices -> multi-color variant textures misalign, indistinguishable
from "no change needed".
Fix: count/surface the skipped records.

---

## Low / latent

### [ ] L1 - ESL-overflow check counts only ARMA (ube_patcher.py:2057-2069)
Correct today (every own-index mint is ARMA; no TXST minted) but coupled - a future
non-ARMA own-index mint silently under-counts and re-opens the overflow-CTD class.

### [ ] L2 - VirtualBody re-hide failure is a `notes` entry only (auto_convert.py:1031-1036)
Possible visible "blue body-double," exit 0.

### [ ] L3 - Deferred postflight checks absent: race-coverage + alt-texture-index residual
Known/deferred per project memory. Leaves invisible-on-UBE-race and wrong-texture-variant
with no last-line check.

### [ ] L4 - Misc swallows
phase-1 HDT/BODYTRI injection (nif_convert.py:2578), phase-2 BODYTRI (9139),
stale-chain-bone guard (2446/9160) - mostly partially backstopped by validate_dst_nif.

---

## What's solid (do not regress)
- Atomic write coverage for ESP/NIF/TRI/OSD/HDT-XML/PEX is complete.
- Merged-Combined postflight CTD gate fails the build for master-ordering, ESL-overflow,
  formid-out-of-range/zero, modt-malformed (auto_convert.py:1849 -> 1911).
- nif_errors, nif_load_failures, esp_gen_failures, nif_partial/dropped_shapes, per-source
  raises, merge-blockers all force exit 2. GUI + frozen exe propagate the code.
- iter_subrecords is bounds-checked.
