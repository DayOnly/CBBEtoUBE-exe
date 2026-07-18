# Session changes 2026-07-08 — 3BA-source fix + layered-cuirass recipe + diagnostics (build22, UNCOMMITTED)

Context: a long clipping session on the layered dark-leather cuirass (4 cloth layers,
shapes Cuirass_A/B/C + Greaves). Most of it was misdiagnosis + guess-and-check; the
durable wins are the SOURCE-SELECTION fix (below — the actual root cause), the
butt-rebalance default flip, and the diagnostic scripts. See memory
`project_3ba_source_selection_bug`, the layered-cuirass working-recipe note,
`feedback_armor_clip_diagnostic`.

## ROBUSTNESS — transient source-open retry (`nif_io.open_nif_retry`, build23)
At 23 workers, eight valid 3-4MB meshes from one high-poly outfit mod reported
"Could not open ... as nif"
(Windows file-share / handle contention or AV scan on a file that opens fine in
isolation) — the converter dropped them on the first blip. New `open_nif_retry`
retries with backoff (0.08→0.64s, 5 attempts) then re-raises so a GENUINELY bad
file still fails loudly. Applied at every SOURCE/REF open (load_nif, the fit-path
`src_nif_for_fit`, the 3BA classifier, the body-ref opens hit by every worker, the
phase-2 `src_nif`/`ube_nif`/`cbbe_ref`). "Too many workers" is no longer a failure
mode. 3 tests (`test_nif_io_retry.py`). Suite 619.

## THE HEADLINE FIX — armor source selection (`discovery.build_mesh_index`)
**Root cause of the clipping.** `build_mesh_index` resolved each armour mesh to the
highest-MO2-priority provider (first-writer-wins). A `<mod> - Bodyslide Output - 3BA`
folder outranks the base mods, so ~26 armours were converted from a **3BA-body-preset-morphed**
source into a UBE target — the 3BA morph squashes the stacked cloth layers together ->
clipping (belt-into-pants, robe-into-pants). The user-approved `.looksgood` only looked
right because it was made from the **raw base** mesh (2.61u different from the 3BA source).
**Fix:** 3-tier stable sort deprioritizes BodySlide-output mods — (0) base/replacers,
(1) UBE outputs, (2) other-body outputs (3BA/HIMBO/NSFW). Base wins; an output still fills
a mesh nothing else provides. Verified: the cuirass resolves to its own base mod,
build22 exe convert == `.looksgood` to 0.008u. Regression test added
(`test_build_mesh_index_deprioritizes_bodyslide_output_source`, 3 cases). Suite 616.
HEURISTIC CAVEAT: keys on the substring `"bodyslide output"` (space) in the mod folder
name — matches the user's output-mod naming; a differently-named output mod would slip through.
**Needs a full reconvert to apply pack-wide.**

## ALWAYS-ON changes (affect every reconvert — reviewed, safe)
- **`_BUTT_REBALANCE` default flipped OFF→ON** (`CBBE2UBE_BUTT_REBALANCE` default "1").
  This is the RESTORE of the original/proven full butt-match. Earlier this session it
  was wrongly defaulted off (a "coverage regression" misdiagnosis). VERIFIED: build21
  interpreted-with-recipe == the user-approved `.looksgood` byte-for-byte (0.0000u);
  exe convert applies the same recipe (Greaves 15 bones, RThigh ~31%, butt+scale bones).
- **morph-TRI scale-graft is opt-in** (`_MORPHTRI_SCALE` default off) = the ORIGINAL
  exemption (a source-TRI shape keeps its stable source skin). Correct default.

## OPT-IN flags added this session — ALL default-off, NONE adopted
Each traded one fix for a new artifact on this tight 4-layer armor. Logic reviewed OK;
listed with their known issue so they're not blindly reused:
- `CBBE2UBE_THIGH_STANDOFF` / `_Z_LO` / `_MEDIAL` — inner-thigh pose clearance. **The
  `_MEDIAL` gate creates a CRINKLE** (sharp inner/outer boundary at ~z63 — armor_clip_diag
  flags 2.3u edge-jump). Do not use the medial gate without push-field feathering.
- `CBBE2UBE_CUIRASS_INFLATE` — per-vertex leg-gated torso puff (legs untouched, verified).
  Leaves a mild boundary crinkle at the cuirass top (~z99). Sound but imperfect.
- `CBBE2UBE_ABDO_JIGGLE_SYNC` — `_sync_abdomen_layered_cloth_weights`: matches an inner
  cloth layer's butt/belly jiggle to the OUTERMOST layer (jiggle-only, base skin kept,
  mass conserved, authority STBs copied). Correct + thigh-safe; not needed once the
  inner-thigh clip was shown to be a pre-existing pose limit.
- `CBBE2UBE_LAYER_STACK_GAP` (was const 0.15, now env) — bigger inter-layer gap opens
  body-gaps at the thigh (armor_clip_diag: newclip Cuirass_C>Cuirass_B). Don't raise it.
- `CBBE2UBE_REAR_STANDOFF_Z_HI`, `CBBE2UBE_OVERLAY_RAW_STRONG` — env-exposed constants,
  defaults unchanged.
- `_butt_match_vert(..., rebalance=)` param — clean split of the rebalance vs jiggle halves.

## New tooling (repo `scripts/`)
- `armor_clip_diag.py` — crinkle / layer-penetration(+inherent-vs-NEW) / off-target /
  thin-clearance / weight-delta. Diagnose + verify modes. Calibrated 5/6 known-bad.
- `convert_one_armor.py` — interpreted single-armor reconvert helper (auto slots+body ref).

## Suite / build
- 615 tests green with all the above; 2 goldens byte-identical (flags don't touch them).
- build21 built + deployed to `D:\Modlists\ARR\tools\CBBEtoUBE\CBBEtoUBE.exe` (hash-verified).

## Recommendation before committing
The always-on part (BUTT_REBALANCE default-on) is the one that matters and is proven.
The opt-in flags are experiments — keep or prune per taste, but the `_MEDIAL` gate should
gain push-field feathering before it's ever recommended.
