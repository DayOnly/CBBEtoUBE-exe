# How the converter works, and the rules for changing it

Operational companion to `DESIGN.md` (why a mechanism exists) and `METRICS.md`
(whether a measurement is sound). This file answers: **what runs, in what order,
what each step can and cannot reach, and what you must not do to it.**

Every ordering below was extracted from the source, not from notes. Function
names are stable; line numbers are not — re-grep the name.

---

## 0. The five rules

These are not style preferences. Each one is here because breaking it cost a
session or shipped a wrong mesh.

1. **A behaviour toggle must be reachable from the GUI.** MO2 passes no
   environment variables, so an env-only toggle can never be switched on by a
   user, therefore never validated in game, therefore never finished. Numeric
   tuning knobs may be env-only. See §6.
2. **Clearance is not the fix for motion clipping.** A 3891-shape census: p50
   clearance 1.55 on clipping shapes vs 1.52 on clean ones — it does not
   discriminate at all. Motion clipping is a FOLLOW problem. Check follow first;
   it is one cheap measurement.
3. **The user's in-game report is ground truth.** Never answer it by
   reclassifying the defect as z-fighting, uncovered-by-design, or a measurement
   artifact. When a measurement disagrees with the screenshot, the measurement is
   what needs explaining.
4. **Bind pose is not the shipped condition.** In game the body is morphed and
   animated, and simulated cloth is placed by physics, not by its rest pose. A
   bind-pose delta is a hypothesis.
5. **Run the pass; do not model it.** Reasoning about what a pass "would do"
   from its gates has overstated a fix by 5x and by 7x on separate occasions.

---

## 1. Routing — phase 1 or phase 2

`convert_nif` is the entry point.

* **Phase 2** (`convert_nif_phase2`) when the NIF has an inline body or exposed
  skin AND a UBE body reference exists. It injects the UBE `BaseShape` +
  `VirtualBody`, drops the CBBE body, and runs the full chain.
* **Phase 1** otherwise — a copy or body-aware rebuild with a shorter chain.

**The injected `BaseShape` is the phase marker.** A converted NIF without one is
a phase-1 piece. This matters for scoping any audit: of 311 physics-XML
candidates in one shipped pack, 201 had no `BaseShape` — they are phase-1 and
structurally cannot be touched by any phase-2 pass.

---

## 2. Phase 2, in source order

### 2a. Per-shape geometry

| # | pass | notes |
|---|------|-------|
| 1 | `bake_preset_into_armor` | k=4 IDW of the preset delta |
| 2 | `fit_armor_to_ube_body` | **OFF** (`fit_armor=False`) |
| 3 | `warp_armor_by_body_delta` | k=4 IDW of the CBBE→UBE delta |
| 4 | `inflate_armor_outward` | push out |
| 5 | `conform_to_source_standoff` | pulls IN *and* pushes OUT |
| 6 | `_smooth_warp_grooves` | smoothing operator, not a target |
| 7 | `snap_armor_outside_body` | legacy fallback, `elif` branch only |
| 8 | `clear_armor_outside_body` | the anti-poke — the final push |
| 8b | `_inflate_cloth_over_bust_butt` | soft-cloth alternative to 8 |
| 9 | `_physics_chain_nowarp_blend` | pins chain verts back to SOURCE |
| 10 | `fit_metrics.minimum_push` | the only measurement-driven push |

**Passes 4 and 5 fight each other.** Measured over 38 shapes: `inflate` is
CANCELLED by `conform` on 12 of 32 instances, every cancellation naming conform,
with `conform` moving the same verts 1–5x further the other way. `conform`'s own
median survival is 0.507 and it never exceeds 0.847 on any shape. Order decides
the winner. This is the strongest argument for consolidating 4/5/8/8b into one
target-offset field — the measured pass-interaction study behind that argument
is kept with the development working notes.

**Pass 9 makes 4/5/8 inert on chain verts.** It pins them to source position so
they stay aligned with chain bones recreated at source bind. Full-chain verts
measure 0.0000u from source at every push budget. Raising a push budget to fix a
chain-driven region cannot work, and has been tried.

### 2b. Skin and bone

`_precreate_custom_bone_chains` (chain nodes; pelvis re-anchor and the optional
`#chain-body-shift` fire inside it) → `add_scale_bone_weights` →
`compute_body_blend_skinning` (the "M6 reskin") → `_slot_aware_*` band/reach →
`_sync_chest_layered_cloth_weights` / `_sync_abdomen_...`.

**Weight passes and position passes are different domains. Do not merge them.**
Conflating them is the `_shape_has_hdt_smp_rigging` bug that cost a session.

### 2c. Cross-shape

`_separate_chest_layered_cloth_depth` → `_separate_abdomen_...` →
`_ride_layers_on_reference` → `_repair_layer_order` → `_conform_cords_to_host`
(off) → `repair_collapsed_tris` → `_weld_cross_shape_seams` →
`_ride_effect_overlays_on_plate`.

### 2d. On-disk, post-save (each re-loads and re-saves the NIF)

`_normalize_partitions_on_disk` → `_split_bust_collider_shape` →
**`_finalize_hdt_physics`** → `_split_bust_collider_xml` →
`_transfer_body_jiggle_to_fitted` → `_conform_fitted_to_body` →
`_match_rigid_leg_bend_to_body` → `_match_leg_motion_to_body` →
`_match_spine_motion_to_body` → `_match_arm_motion_to_body` → `validate_dst_nif`.

`_finalize_hdt_physics` must stay before the graft (which reads the XML to decide
what is a collider) and last among the extra-data writers.

---

## 3. Phase 1, and what it genuinely skips

`add_scale_bone_weights` → `warp_armor_by_body_delta` → `inflate_armor_outward` →
`conform_to_source_standoff` → `_smooth_warp_grooves` →
`_physics_chain_nowarp_blend` → reskin/band → `detect_zfight_pairs` → layered
separate/sync → `_weld_cross_shape_seams` → `_ride_effect_overlays_on_plate` →
`_precreate_custom_bone_chains` → `_finalize_hdt_physics` → the jiggle/conform/
motion-match on-disk group.

**Phase 1 has NO anti-poke** (`clear_armor_outside_body` is never called) and no
`bake_preset_into_armor`.

**It also does NOT conform by default.** A `conform_to_source_standoff` call site
exists in the phase-1 path, but it is gated on `PHASE1_CONFORM`, which is default
OFF — so a stock phase-1 piece inflates with nothing to reel it back to the
author's fit. (An earlier revision of this file claimed phase 1 conforms. That
was derived by listing call sites without evaluating their gates — the presence
of a call is not evidence that it runs. Corrected 2026-08-01.)

---

## 4. What a pass can and cannot reach

Most "this pass doesn't work" reports are really "this pass never ran".

* **Colliders are never grafted or reskinned.** A per-triangle collider is the
  surface the body's own physics collides against; grafting motion onto it
  closes a feedback loop. Measured in game: breasts tore off and fell through
  terrain.
* **Simulated verts are never grafted.** `is_chain[i]` is true when any
  non-skeleton bone holds >0.1 — a simulated vert has no rest position to follow
  the body from.
* **Every skin pass skips SMP/collider/soft-body shapes.** Structural, not a bug.
* **`_conform_fitted_to_body` can only SHRINK a vert's bone set.** It rebalances
  bones a shape already has; it can never add one. Any pass that defers a
  missing-bone problem to it is deferring to something that cannot fix it.
* **Hands/feet take the fine-animation branch** — warp + inflate only, then
  `continue`. They never see anti-poke, conform or the layer passes.

---

## 5. Which metric answers which question

| question | tool | notes |
|---|---|---|
| is the garment behind the skin? | `clipping_report` | area-weighted; has the body-occlusion gate |
| ... fast, in-pipeline | `standoff_audit.ClipTester` | must stay equal to the above; `selftest` compares totals AND depth bands |
| ... inside the converter | `fit_metrics._ClipTester` | **no occlusion gate** — safe only while the push region stays on thick front torso |
| is it too far OFF the body? | standoff median/p90 | clipping has no upper bound; over-inflation scores 0.0% |
| will the body punch through under morph? | follow ratio | `scripts/analysis/find_morph_follow_gaps.py` |
| did a pass survive to the shipped verts? | `CBBE2UBE_SURVIVAL_TRACE=1` | names the canceller |

**Split any clipping number by depth before concluding.** Sub-0.2u and >1u need
corrections an order of magnitude apart. Depth is NOT a cosmetic/real
discriminator — sub-0.2u penetration is visible in game; only the zoom test
settles that.

**`uncovered` is not "bare by design".** Both rays escaping also catches a vert
whose garment sits beside it, and anything that only pokes through under morph.

---

## 6. The flag surface

215 `CBBE2UBE_*` names; 39 GUI-exposed; 77 boolean toggles of which 48 are
env-only. 28 of those are `NO_*` kill-switches (fine — bisect tools) and 3 are
diagnostics (fine). The remaining **17 are opt-in features nobody can enable**.

Several document themselves as "default OFF until proven in game" while being
impossible to turn on in game. That is a deadlock, not caution.

**Rule: an opt-in intended to ship gets a `src/gui_settings.py::SETTINGS` entry
in the same commit, or it does not get written.**

---

## 7. Dead ends — do not retry these

* **Full body as an SMP collider.** `_ensure_cloth_body_collider` exists,
  default off. Tried in game: the sim destabilised and a body collapsed to the
  floor. A full-body collider paired with cloth also skinned to that body
  diverges in FSMP. A region-limited kinematic sub-mesh collider is the untried
  successor.
* **SMP margin/threshold tuning for a floor-length skirt over swinging legs.**
  Three settings tested in game; no usable window between under-catching and
  ejecting the drape. FSMP ceiling, not a converter bug.
* **Raising a push budget to fix a chain-driven region.** Inert by construction
  (§2a pass 9), measured identical at 1.0 and 2.0.
* **Warping chain bones individually.** Changes inter-bone rest lengths and is
  how a chain explodes. Shift a chain's ROOT and it translates rigidly —
  measured worst inter-bone change 0.000000u.
* **Grafting jiggle onto a collider.** See §4.

---

## 8. Checklist for changing a pass

1. Does a metric already answer this, and is it the right one for the symptom
   (§5)? Motion/morph symptom → follow, not clearance.
2. Can the pass even reach the shape (§4)? Check before tuning anything.
3. Is it already tried? Search the worklog and the dead-end list (§7).
4. Write the GUI entry with the flag (§6).
5. Measure by RUNNING the pass over a population, not by modelling its gates.
   Separate "has the symptom" from "has this cause" before quoting a class size.
6. Handle failure the way the siblings do — `_note_pass_failure`, not a bare
   `except: pass`. A pass that dies silently reads exactly like a pass with
   nothing to do.
7. `scripts/golden_output.py check` — an unintended diff is a regression; an
   intended one must be explainable shape by shape.
8. Ship it off by default and get an in-game verdict before defaulting it on.
