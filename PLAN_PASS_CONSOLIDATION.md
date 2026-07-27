# Pass consolidation — findings and plan (2026-07-27)

What the vertex passes actually do to a mesh, which of them rewrite each other's work,
and a sequenced plan for simplifying it without breaking output.

Everything here is measured on real conversions of four pieces — a fitted dress, two
vanilla-style cuirasses, and a robe — instrumented per pass.

---

## 1. What the chain does

32 pass-like calls run from `convert_nif_phase2`. The vertex-moving core, in order:

| pass | calls | verts | moved | mean move | max |
|---|---|---|---|---|---|
| `bake_preset_into_armor` | 18 | 49,724 | 0.9% | 0.130 | 0.76 |
| `warp_armor_by_body_delta` | 24 | 53,704 | **100%** | 0.386 | 4.59 |
| `inflate_armor_outward` | 18 | 49,724 | 82.9% | 0.345 | 1.04 |
| `conform_to_source_standoff` | 18 | 49,724 | 84.6% | **0.610** | 4.20 |
| `_smooth_warp_grooves` | 18 | 49,724 | 94.5% | 0.098 | 4.40 |
| `clear_armor_outside_body` | 4 | 36,884 | 78.7% | **0.024** | 1.81 |
| `_physics_chain_nowarp_blend` | 18 | 49,724 | 16.9% | 0.471 | 3.29 |

**CHURN = 2.27x.** Total path travelled 67,293u; net displacement 29,658u. The mesh is
pushed more than twice as far as it ends up moving.

## 2. Which passes fight — measured, not guessed

Per-vertex displacement vectors, correlated over verts BOTH passes moved:

| cos | shared verts | pair |
|---|---|---|
| **−0.949** | 35,348 | `inflate_armor_outward` vs `conform_to_source_standoff` |
| −0.354 | 41,568 | `warp_armor_by_body_delta` vs `conform_to_source_standoff` |
| −0.091 | 23,862 | `conform_to_source_standoff` vs `clear_armor_outside_body` |
| +0.259 | 46,560 | `warp_armor_by_body_delta` vs `inflate_armor_outward` |

`inflate` and `conform` are **almost exact opposites** on 35k vertices. Inflate pushes
every vert out ~0.345u; the very next pass pulls back ~0.610u along the same axis. That
single pair accounts for most of the 2.27x churn.

## 3. But they are NOT redundant — the obvious conclusion is wrong

The tempting read is "delete the inflate, the conform undoes it anyway". **Tested by
converting every piece twice with the inflation magnitude forced to zero:**

| | result |
|---|---|
| verts whose FINAL position changes by >0.05u | **22.8%** |
| verts whose FINAL position changes by >0.20u | **12.2%** |
| max change | 3.63u |

So the conform pulls back *most* of the inflate but not all of it, and ~23% of vertices
genuinely end up further out because the inflate ran. Deleting it would move those verts
INWARD — straight into the clipping class this project spends its time fixing.

**Conclusion: this is a candidate for a MERGE, not a deletion.** Push-then-pull-back is
an expensive and opaque way to express "put each vert at max(source standoff, minimum
clearance)", but the current output depends on the exact arithmetic of doing it in two
steps. Any merge has to reproduce that arithmetic, which means it needs a regression
harness before it is attempted, not after.

---

## 4. The plan

Ordered so that every risky step is preceded by the thing that makes it safe.

### Step 1 — Golden-output regression harness (PREREQUISITE, no behaviour change)
Convert a fixed set of ~20 pieces spanning the classes (plain cuirass, layered cloth,
SMP collider, chain-welded, soft-body, hands/feet, glow) and store a per-shape hash of
final vertex positions + weights. A script re-converts and diffs.

Without this, no consolidation below is verifiable — the whole reason the
inflate/conform pair cannot simply be merged today is that nothing would catch a 0.2u
regression across 23% of verts. **This step is worth doing even if nothing else here
happens.**

### Step 2 — Narrow the 13 wide swallowed exceptions (independent, low risk)
Four geometry passes wrap their ENTIRE body in `except Exception: return 0`
(`_separate_abdomen_layered_cloth_depth` guards 182 statements). A `NameError` inside
one is indistinguishable from "nothing to do" — this already happened once today in
`write_conversion_summary`. Log at debug level rather than narrowing, so the
best-effort contract is unchanged.

### Step 3 — Consolidate the near-duplicates (low risk, mechanical)
- `_clr` / `_src_clr` / `_signed` — three near-copies (0.99 / 0.95 / 0.95) of a
  clearance computation, living inside the same layered-cloth passes as Step 2.
- `gui.py` `_apply_filter` / `_ov_apply_filter` (0.992) and `_set_all` / `_ov_set_all`
  (0.986).
- `_find_ube_template_body` / `_find_ube_body_osd` (0.924).

### Step 4 — Decide the remaining env-only feature flags
`CORD_CONFORM_ENABLED` (measured, needs tests then a GUI row),
`THIGH_STANDOFF_MEDIAL` (dead behind a parent that is itself 0.0 and env-only —
decide the parent), and the 7 default-ON kill switches that cannot be reached from the
GUI for bisection. `CHAIN_SKIRT_PHYSICS` stays quarantined.

### Step 5 — THEN attempt the standoff merge (highest value, highest risk)
Only with Step 1 in place. Replace `inflate_armor_outward` +
`conform_to_source_standoff` with a single solve that computes each vert's target
standoff directly. Success criterion is not "looks right" — it is **byte-identical or
explained** against the golden set, with any deliberate difference measured and
justified.

Expected gain: churn 2.27x → close to 1.0, one clearance concept instead of two
opposed ones, and a single place to tune standoff instead of two that cancel.

### Step 6 — Pair the 32 source-text tests with behavioural ones
Tests asserting on `inspect.getsource` pin wording, not behaviour. They are legitimate
for proving a guard is wired in, but each needs a behavioural partner. Several written
today do not have one.

---

## 5. What NOT to do

- **Do not delete `inflate_armor_outward`.** Measured: 22.8% of verts would move inward.
- **Do not merge passes before Step 1.** The inflate/conform arithmetic is load-bearing
  and nothing currently detects a regression in it.
- **Do not "simplify" the 176 narrow swallowed excepts.** They guard 1–2 statements and
  are genuinely best-effort; only the 13 wide ones are a problem.
- **Do not trim `_CONFORM_SKIP_NAMES`** — "unused in this pack" is not "unused".

## 6. Method note

Two findings in this document were nearly reported wrong:

1. **"The final anti-poke never runs."** It reported zero calls across three
   conversions. Cause: `convert_one_armor.py` resolved `biped_slots=0` because those
   BodySlide mods ship no ESP, and the pass is gated on slot 32/49. A harness artifact,
   not a converter bug — the same class as the `meshes/` ancestor problem found earlier
   today. Re-run with a real slot mask, it runs on 36,884 verts.
2. **"Inflate is undone by conform, delete it."** cos −0.949 makes that look obvious.
   Forcing the magnitude to zero and diffing the final meshes disproved it.

Both were caught by testing the claim rather than the correlation. The pattern is
consistent enough to state plainly: **a measurement that suggests deleting something
should be followed by actually deleting it in a scratch run and measuring the
difference.**

---

# Step 1 BUILT — `scripts/golden_output.py` (2026-07-27)

    python scripts/golden_output.py capture      # record the baseline
    python scripts/golden_output.py check        # re-convert and diff; exits 1 on regression

15 pieces, 360k vertices, chosen to span the classes that behave differently:
per-triangle collider, per-vertex soft-body, layered cloth, chain-welded skirt,
draping-named, multi-shape mashup, 1st-person, fitted bodice with XML-declared
colliders, ordinary rigid cuirass, robe with NO physics XML, hands, feet.

Compares **magnitudes, not hashes** — max/mean vertex displacement, bone list changes,
and per-bone weight totals — because a 1e-7 float wobble must read differently from a
0.2u shift.

## It avoids both traps that bit this project today

- **Explicit biped slots per piece.** `convert_one_armor.py` resolves slots from the
  mod's ESP, and a BodySlide-output mod has none, so slots=0 and
  `clear_armor_outside_body` (gated on slot 32/49) silently never runs. That produced a
  false "the final anti-poke never runs" earlier today.
- **A `meshes/` ancestor.** Physics-XML resolution walks up to a directory literally
  named `meshes`; without one, collider detection returns an empty set and every
  collider protection no-ops.

## It distinguishes a CODE change from an INPUT change
Source NIFs are hashed into the manifest. If a mod updates, `check` says
"SOURCE CHANGED -- not a code regression" instead of blaming the converter. The
`CBBE2UBE_*` flag set is recorded too, and `check` refuses to compare across a
different one (verified: it correctly refused when a flag was dropped).

## Validated — it catches regressions, at known sensitivity

A net that has never caught anything is not a net. Injected changes to the inflation
magnitude:

| injected change | pieces flagged | max shift detected |
|---|---|---|
| −0.14% | 1 / 15 | 0.0017u |
| −1% | 1 / 15 | 0.0119u |
| −5% | 1 / 15 | 0.0595u |
| −10% | 1 / 15 | 0.1192u |
| **removed entirely** | **13 / 15** | **3.6261u** |

Clean re-runs pass byte-identically, so the conversion is deterministic.

**The 1-of-15 rows are informative, not a gap.** Small magnitude changes are absorbed
by the downstream `conform_to_source_standoff`, which clamps to the SOURCE standoff —
so for most verts the final position does not depend on the inflate magnitude at all.
Only where the inflate is the binding constraint does a small change survive. That is
independent evidence for the merge case in §3: the pair really is computing
`max(source standoff, minimum clearance)`, and the outward push is mostly saturated.

`gloves` and `boots` correctly never flag — the hands/feet branch uses a separate
constant (`ARMOR_INFLATION_MAGNITUDE_HANDS_FEET`) the injection did not touch.

## Housekeeping
`golden/` is gitignored: it is generated data, and its manifest records which MOD each
source mesh came from, so it falls under the same mod-agnostic rule as the working
notes. The harness deletes its scratch conversions — keeping them cost 105 MB per run;
the baseline itself is 3.7 MB.

**Step 5 (the standoff merge) is now unblocked.**
