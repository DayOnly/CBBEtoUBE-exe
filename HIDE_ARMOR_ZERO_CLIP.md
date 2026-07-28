# Hide armour to zero clipping — working document

Target sample: `armor/hide/f/cuirass{light,medium,heavy,heavychieftain}_{0,1}.nif`.
Chosen because it is a confirmed in-game offender, it is vanilla (every playthrough
sees it), and it has already defeated three separate fixes — so it is a fair test of
whether a method is real.

**Status: diagnosis complete, not yet fixed.** This document records the mechanism and
the protocol. It is not a claim of success.

**Scope rule:** hide is the PROBE. Anything that ships must be gated on a measurable
property that generalises. A per-piece hack is not an acceptable outcome.

---

## 1. What the piece actually is

| shape | role | bones |
|---|---|---|
| `BaseShape` | injected UBE body | 36, incl. `L/R Breast01-03`, `NPC L/R Butt`, `NPC Belly` |
| `CuirassLight` | the garment — **and its own SMP collider** | 59, **zero jiggle bones** |
| `HideCollision` | leg/skirt collider | 7 |
| `Stabilizer` | helper | 12 |

The armour XML (`armor/hide/f/cuirasslight.xml`, 629 lines) contains **no
`per-vertex-shape` at all** — the garment has no simulated cloth. The skirt is
BONE-CHAIN cloth (127 bones, 148 constraints). Two colliders only:

* `CuirassLight` — `<tag>Fabric</tag>`, `no-collide-with-tag Fabric`, plus arm-bone
  exclusions. **Declares no `can-collide-with-tag` at all.**
* `HideCollision` — `<tag>virtuallegs</tag>`, `can-collide-with-tag Fabric`.

## 2. What the body is (third-party, we do not control it)

`CBBE 3BA (3BBB) - Config`, two files:

* `CUSTOM CBBE SMP.xml` — three shapes only: `VirtualGround`, `Baseshape` (`<tag>body</tag>`),
  `Labia`. All per-triangle colliders, no simulated cloth.
* `3BBB-Amazing.xml` — the body collider set: `3BBB` (`body`), `3BCA_Breast` (`3BCA_Br`),
  `3BCA_Butt` (`3BCA_B`), head/hand/feet/arm/leg, and **8 breast bone rigid bodies**.

**Neither body file declares a single `can-collide-with-tag`.** Collision is therefore
opted into from the GARMENT side — which is exactly what 203 of the 231 soft-body
cloth XMLs in our output do (`can-collide-with-tag: body`).

## 3. The mechanism, and why it is not a conversion regression

The breast is a driven bone chain. The garment is rigid, skinned to Spine/Clavicle,
with **zero jiggle bones**, and **nothing anywhere declares that the breast may collide
with `Fabric`**. So the bust travels and the leather does not, and nothing stops it.

Two independent measurements agree, and both say the same thing — the defect is in
MOTION and invisible at rest:

* bind-pose clearance on `cuirasslight` is **clean**: breast +0.88u mean, 0.6% of
  breast verts poking. Its siblings measure *worse* at rest yet are reported less.
* the SMP bust motion envelope permits up to **6.0u** of travel against **~1.0u** of
  clearance.

The source armour has no breast bones either, so this is an **unfixed capability gap,
not something the conversion broke**.

## 4. Three fixes already tried — do not repeat them

| # | attempt | outcome |
|---|---|---|
| 7 | XML bone-name remap (3BA `NPC L Breast` → UBE `L Breast01`) | **premise DISPROVEN** — XPMSSE's skeleton carries both, and the body config drives both schemes. Now opt-in (`CBBE2UBE_XML_BONE_REMAP`, default OFF). |
| 7b | static clearance / collider-preserving vertex push | improved the numbers (6.3%→3.3% exposed), **did not resolve the report**. Opt-in. |
| 7c | torso jiggle graft (`_transfer_body_jiggle_to_fitted`) | landed, then **REVERTED** — grafting onto a shape that IS its own collider creates a feedback loop (cloth moves collider, collider pushes cloth); breasts tore off in game. |

Current shipped state: **59 bones, no jiggle bones** — i.e. 7c is rolled back.

## 5. Why our offline harness cannot score this piece

Established 2026-07-28: the pose harness poses the SKELETON and applies linear blend
skinning; it does **not** simulate SMP. `cuirasslight` carries **1.6% of its thigh
weight on posed bones** (98.4% chain-driven), so the harness holds the garment still
and reports a 69–73% thigh loss that is pure artifact. Pack-wide, **54% of
armor-regions are in this blind spot**.

**Consequence for this task: no existing offline number can verify "zero clipping" on
this piece.** Any claim of success has to come from an in-game A/B, or from a metric
built specifically for the motion case. This is the single most important constraint
on the work and it is why previous attempts "improved the numbers and not the picture".

## 6. Candidate levers, ranked

1. **Declare the collision the config is missing** — give the garment collider a
   `can-collide-with-tag` for the body's breast/butt tags (`3BCA_Br`, `3BCA_B`, `body`).
   This is the only lever that attacks the measured mechanism rather than its symptoms,
   and it adds no geometry, so it cannot look baggy.
   **RISK, known:** unconstrained cloth+body pairs have caused an FSMP out-of-bounds
   equip-CTD before; that is why pair emission is gated. Any change here must go
   through the existing XML-emit gate, not around it.
2. **Bust motion envelope clearance** — widen until the garment contains 6.0u of
   travel. Rejected on appearance grounds (baggy) and already measured as the weaker
   lever; recorded only for completeness.
3. **Jiggle graft with a collider-safe formulation** — the 7c idea needs a way to
   break the feedback loop (e.g. graft only verts that are not part of the collider
   surface the skirt rests on). 7c already established a 4.0u chain standoff; the
   revert was about the collider role, not the standoff.

## 6a. The comparative finding — hide ships no bust collider, its siblings do

Found by diffing hide against the vanilla armours with the SAME structure
(collider-only XML, no simulated cloth of their own), rather than by reasoning about
HDT-SMP semantics:

| armour | garment over the bust | bust collider present |
|---|---|---|
| `armor/bandit/body1f` | `Top` — 212 verts in z 88-104 | **`TopProxy` (12) + `TopCol` (48)** |
| `armor/dwarven/dwarvenarmorf` | none in band | `Proxy` z 37.9-73.1 |
| `armor/elven/f/cuirass` | none in band | `Proxy` z 32.6-81.8 |
| **`armor/hide/f/cuirasslight`** | **`CuirassLight` — 408 verts in z 88-104** | **NONE** — `HideCollision` is z 8.4-56.7, legs only |

Hide covers the bust *more* than the sibling that ships bust colliders, and provides
none. Its only body-side collider ends 31u below the breast.

Those proxy shapes are AUTHORED in the source, not generated by us — so this is a
source authoring gap, and the question is whether the converter should synthesise one.

**This is a path already identified and explicitly never built.** A FULL-BODY collider
injection was tried and failed in game (the body collapsed); the note records the chest
sub-mesh as unbuilt. Bandit supplies the missing scale: **24-160 verts**, a chest patch,
not a body.

## 7. Protocol for claiming zero

A number is not evidence here (§5). To claim zero on this piece:

1. **Define the pose set in game**: idle, walk, sprint, crouch, jump-land, and the
   side view that the 7c report specifically called out.
2. **A/B one lever at a time**, re-equipping between arms — the mesh cache has faked
   "not applying" before; re-equip or save→menu→load every time.
3. **Negative control**: a piece with no reported defect must not regress.
4. **Record the failure mode, not just pass/fail** — "breast at side under movement"
   is what distinguished 7c from 7 and 7b, all three of which "passed" on static
   numbers.
5. Only then generalise: identify the measurable property that selects this class
   (own-collider torso garment with zero jiggle bones under a simulated bust) and
   validate across that population before shipping.
