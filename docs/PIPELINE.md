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

`_precreate_custom_bone_chains` (chain nodes; the pelvis re-anchor and the two
optional root moves — `#chain-body-shift` then `#chain-rest-outside-body` — fire
inside it, in that order, and compose) → `add_scale_bone_weights` →
`compute_body_blend_skinning` (the "M6 reskin") → `_slot_aware_*` band/reach →
`_sync_chest_layered_cloth_weights` / `_sync_abdomen_...`.

**Weight passes and position passes are different domains. Do not merge them.**
Conflating them is the `_shape_has_hdt_smp_rigging` bug that cost a session.

**WHOEVER CREATES A NODE FIRST WINS, and the chain writer needs its PARENT.**
Two coupled facts, each of which has cost a shipped defect:

* pynifly exposes no node-transform setter that survives a save, so a node
  created flat at identity by `add_bone` during skin install can never be moved
  afterwards. `_seed_flat_chain_anchors` exists to win that race and runs into
  the still-empty NIF (`#anchor-global-fix`).
* the chain writer at the end of `_precreate_custom_bone_chains` attaches a bone
  only once its PARENT is in `existing`, and gives up when a round adds nothing.
  So deleting the flat branch's anchor creation did not cost one bone — it cost
  every chain hanging off that anchor, and shipped as skirts stretched from the
  hip to the origin (`#chain-anchor-recreate`: 44 pieces, 167,920 verts).

Generalises past chains: **before deleting a node-creating call, ask what LATER
code assumes that node exists.** The deletion behind this was right about the
case it described (an anchor that already exists, whose global is genuinely
unrecoverable) and silent about the case it also removed (one that does not).

### 2c. Cross-shape

`_separate_chest_layered_cloth_depth` → `_separate_abdomen_...` →
`_ride_layers_on_reference` → `_repair_layer_order` → `_conform_cords_to_host`
(off) → `repair_collapsed_tris` → `_weld_cross_shape_seams` →
`_ride_effect_overlays_on_plate`.

### 2d. On-disk, post-save (each re-loads and re-saves the NIF)

`_normalize_partitions_on_disk` → `_split_bust_collider_shape` →
**`_finalize_hdt_physics`** → `_split_bust_collider_xml` →
`_conform_collider_to_body` → `_add_butt_collider_patch` →
`_add_skirt_collider_proxy` → `_transfer_body_jiggle_to_fitted` →
`_conform_fitted_to_body` → `_match_rigid_leg_bend_to_body` →
`_match_leg_motion_to_body` → `_match_spine_motion_to_body` →
`_match_arm_motion_to_body` → `_match_spine_twist_to_body` →
`_match_full_weights_to_body` → `validate_dst_nif`.

`_finalize_hdt_physics` must stay before the graft (which reads the XML to decide
what is a collider) and last among the extra-data writers.

The three collider passes (added 2026-08-10) sit straight after
`_split_bust_collider_xml` for the same reason it does: `_finalize_hdt_physics`
overwrites the XML with the authored copy AND re-imports the collider shapes, so
anything earlier is discarded. The butt patch and the skirt proxy APPEND to the
XML that finalize wrote.

Defaults as of 2026-08-11: `_add_butt_collider_patch` and
`_add_skirt_collider_proxy` are **ON** (equip-tested, then judged in game on the
piece the defect was reported against); `_conform_collider_to_body`
(`#collider-shrinkwrap`) stays **OFF** — it is kept only because the leg
expansion it performs is real, and it is not a butt fix (see §7).

**Family-match order is load-bearing** — each rescales the bones it does not
manage, so the last to run wins the overlapping rows: leg → spine → arm →
spine-twist → full-vector. The full-vector match manages EVERY shared bone, so
nothing may run after it. A test pins the whole order.

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

Counted 2026-08-11 over flags the code actually **reads** (`os.environ.get`), not
every name a comment mentions — the earlier figures here (215 / 39 / 28 / 17)
were a looser count and are superseded:

| | |
|---|---|
| `CBBE2UBE_*` flags read by `src/` | 286 |
| GUI-exposed | 54 |
| env-only | 232 — of which 34 `NO_*` kill-switches and 13 diagnostics |
| boolean OPT-INS (default OFF, enable something) | 34 |
| ...of those, **unreachable from the GUI** | **20** |

Every GUI setting's env var IS read by `src/` — no dead rows. The unreachable 20
are the number that matters: several document themselves as "default OFF until
proven in game" while being impossible to turn on in game. That is a deadlock,
not caution.

Re-count rather than trusting these figures — they drift with every commit, and a
stale count here survived several audits. The method: collect
`os.environ.get("CBBE2UBE_…")` across `src/*.py`, and subtract
`{s.env for s in gui_settings.SETTINGS if s.env}` for the env-only set.

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

  **But "rigid" is only true WITHIN a chain, and the chains are not always
  independent** (2026-08-11). On the studded cuirass 74 of 130
  `generic-constraint`s are CROSS-CHAIN: the skirt is a hoop of ten panels
  stitched to their neighbours at the `_01` and `_02` rings. Lifting six of
  them by three different amounts changed 28 inter-panel rest distances by up
  to 1.651u. That is safe **here**, and the reason is worth knowing before the
  next such change: every cross-chain constraint uses `frameInLerp`, so FSMP
  derives its rest frame from the bones AT LOAD and the ±1u linear limits are
  measured from wherever the panels then sit; every constraint with an explicit
  `frameInA` is INTRA-chain, and those rest lengths change 0.000000u. Check
  that pairing in the emitted XML before shifting roots differentially — a
  differential lift under explicit cross-chain frames would fight the solver.
* **Grafting jiggle onto a collider.** See §4.
* **MOVING a collider's existing verts to close a coverage gap** (2026-08-10,
  three ways, all measured). Nearest-point projection closed 0.12u of a 2.89u
  gap; standoff enforcement closed nothing (the collider was already OUTSIDE the
  body, just outside a different part of it); radial shrink-wrap
  (`_conform_collider_to_body`, kept default OFF) moved 50 verts, every one of
  them on the LEGS, and not one rear vert moved rearward. **A collider that lacks
  geometry in a region cannot be made to cover it by moving what it has** — that
  one carried only 10 rear verts in the whole band z62-72 and none at the apex.
  Add geometry (`_add_butt_collider_patch`) or do nothing.
* **Fixing chain-driven cloth from the BODY side alone.** Also 2026-08-10. With
  collider coverage measured complete (0.0% uncovered z44-80) and the standoff
  morph-tracked, the cloth still clipped, because the chain bones' REST POSE sits
  inside the body — the solver pulls in every frame while collision pushes out.
  Adding push cannot win against the pull. See
  `#chain-rest-pose-inside-body` in the worklog before touching this again.
  The answer to it is `#chain-rest-outside-body`, and the two things it had to
  get right are recorded there: the margin must be the body's own outward MORPH
  amplitude (the converter never sees the player's preset, and 6 of the 8
  penetrations only exist under it), and that amplitude must be CAPPED — the
  belly's runs to 8.7u and recruited two front chains measured +3.63u clear.
* **Restoring a chain bone's AUTHORED clearance** (the bone-space analogue of
  `conform_to_source_standoff`). Refuted before building, 2026-08-11: the
  largest losses are on the FRONT skirt (2.96u) where nothing clips, and 10 of
  63 bones were ALREADY inside the source body, because an author routinely runs
  a skirt's bones down the INSIDE of its cloth. The two bones that actually clip
  do not appear in the top 16 losses. Note also that the source's bundled body
  ships **all 6463 normals zero**, so a signed distance taken from stored
  normals reads exactly +0.000 for every bone and looks like a clean
  measurement — derive normals from the triangles.

* **Forcing a stacked layer group onto a SHARED BONE BASIS** so its members
  deform identically (`CBBE2UBE_LAYER_STACK_SHARED_BASIS`, built and left OFF,
  2026-08-11). It WINS on the target metric — inter-layer weight-row divergence
  on all five pairs of a test cuirass fell to ≤0.032, below their 1.2 values,
  beating the shipped fix. It also guts the pass: breast/butt/belly follow
  1208 → 155 on the main layer (−87%) and body-follow 7x worse than EITHER
  baseline. Structural, not a threshold — the layers form one connected group,
  so the shared set collapses to what an 8-bone accessory and a 9-bone plate
  have in common, and every layer renormalises onto that stub. The shipped fix
  shares the ANCHOR instead and leaves each shape its own basis.
  **The general lesson: this one scored better on the metric it was built for
  and was still wrong. A fit change needs a counter-metric aimed at what it
  could plausibly destroy, not just the number it targets.**

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
   nothing to do. **This includes the FINAL SAVE**: `return 0` is already the
   pass's word for "nothing qualified", so a swallowed `atomic_nif_save` makes
   a lost write indistinguishable from a clean no-op. Seven passes shipped that
   way until the 2026-08-11 audit; `tests/test_no_silent_save.py` is a ratchet
   that keeps the save surface honest, and it is negative-controlled (silence a
   handler and it names the function and line).
   Use `_note_pass_failure`, not `print`: a pool worker's stdout can be
   discarded by the frozen exe, while the recorder also reaches the per-piece
   report.
7. `scripts/golden_output.py check` — an unintended diff is a regression; an
   intended one must be explainable shape by shape.
8. Ship it off by default and get an in-game verdict before defaulting it on.
