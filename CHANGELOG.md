# Changelog

## 1.2 — unreleased

The theme of this release is **measuring fit correctly, then fixing what the
measurement exposed.** Most of 1.1.x's fit work was steered by two metrics that
turned out to be anti-correlated with what the game actually shows, so a run
could get "better" numbers and worse armour. Replacing them uncovered a frame
bug that had been corrupting the entire phase-2 pass chain.

### Read this before trusting the numbers

The fit figures below come from the mesh harness, in bind pose, on a specific
body. They are necessary but **not sufficient** — bind-pose metrics are blind to
what animation does, and the worst remaining clipping lives on SMP/soft-body
cloth that no skin pass can reach. In-game verification is still the gate.

### Fixed — the pass chain was computing against a misplaced garment

`shape_body_offset` adds a shape's NiAVObject translation to put its verts into
body space. For a skinned shape whose verts are *already* in body space and
whose `global_to_skin` is identity, that translation is not a correction — it is
a displacement. One piece was being shoved 40 units sideways before a single fit
pass ran, so all twelve phase-2 passes measured, pushed, and inflated against a
garment that was not where they thought it was.

The offset is now checked geometrically: if applying it moves the shape
*further* from the body than leaving it alone, it is discarded. On the piece
that had resisted diagnosis for three months, this alone took bust clipping from
**8.87% to 0.00%**, with the new targeted push pass firing on nothing — there
was nothing left to fix.

This is also why several earlier "the fix didn't work" results were misleading:
the fixes were fine, the geometry they were applied to was not.

### Fixed — groove smoothing pulled the bust apex onto the skin

`_smooth_warp_grooves` does a roughness-weighted Laplacian smooth of the
CBBE→UBE displacement field to remove indent lines. Over a convex feature that
field peaks at the apex, and smoothing a peak flattens it — pulling the garment
back onto the body. The roughness weighting made it worse, because roughness is
highest at that same apex, so the pass smoothed hardest exactly where flattening
does the most damage.

A per-pass trace found it regressed fit on **13 of 42 shapes** (net +1052
exposed verts, worst +394) while improving fit **zero** times.

It is now one-sided: a vert may be smoothed along the surface or away from the
body, never toward it. On the reproduction case, 31 → 161 exposed verts becomes
31 → 21, while 81% of the smoothing motion is retained — the pass still does its
job. `CBBE2UBE_GROOVE_ONESIDED=0` restores the old behaviour for comparison.

### Changed — the fit metric

- **Added** the validated clipping test: a body vert is clipping when the
  garment sits *behind* the skin (ray out escapes, ray in hits a
  same-facing triangle). Calibrated against a user-confirmed clean/dirty pair:
  reads 0.00% on the clean armour, 8.87% on the one that clips.
- **Added STANDOFF** as the counter-metric. Clipping has no upper bound, so an
  over-inflated garment scores a perfect 0.0% — which is exactly how
  over-inflation reached the user twice without any number complaining. Anchor
  from the clean armour: median 1.15u, p90 1.52u.
- **Retired** `bust_verdict.py` and `postflight_1_2.py` off the discredited
  signed-distance path.
- **Retired the ray cone from `underbust_census.py`**, its last live consumer.
  The `surrounded` / `partial` / `bare` columns are replaced by `clipping`;
  rows written before 2026-07-29 are not comparable on those columns. Its
  negative control turned out to be blind to the very bug it was written for —
  a garment below z 80 produces no ray hits in *either* direction, so
  transposing the two directions left it passing. A positive control (densest
  band coverage must read mostly COVERED) was added, and verified to fail under
  a deliberately inverted metric while the negative control still passed.
- **Deleted** `mesh_penetration.containment`, `poke_report`, and `cone_dirs`
  (−155 lines).
  Both were anti-correlated with in-game ground truth and had zero callers and
  zero tests. `surface_penetration` stays: only its *sign* was bad, and the
  census uses its distance. The reasoning that discredited them is preserved in
  `docs/METRICS.md`, and the four metric requirements written for `poke_report`
  moved onto `clipping_report`, which actually satisfies them.
- **Added** an orientation gate that removes a false positive under morph
  (an inward ray striking the far side of the garment past a cut rim) without
  moving the calibration anchors.

### Added — a fit contract over the whole pass chain

The pipeline now states, per shape: **diagnose** what was wrong before anything
ran, **treat**, then **verify** the result and roll back to the best intermediate
state if the chain as a whole made things worse.

Applied to the chain rather than to each pass, because the per-pass version is
measurably the wrong contract. Over 48 traced shapes, exactly one pass ever
regressed bust fit (`conform`, 5 times), every one of those was recovered
downstream, and **0 of 48 shapes ended worse than they started** (total exposure
8138 → 245). `conform_to_source_standoff` pulls IN by design and later passes
push back out; reverting it would have blocked a correct pass and biased every
garment looser — which is the over-inflation reported twice from the game.

Cost: **two measurements per armed shape** instead of eleven. Checkpoints are
array copies, not measurements, so the chain remembers every pass and only pays
to inspect them when the final verify actually fails.

What it deliberately does not do is skip passes when the entry diagnosis looks
clean. Bind-pose clipping is blind to animation — "at rest" in game is an
animated pose — so gating passes on it would trade a measurable defect for an
unmeasurable one.

The per-pass guards on the anti-poke and the soft-cloth inflate were removed in
favour of this: neither ever regressed across the traced sample, and the chain
verify covers the outcome for a quarter of the measurements.

### Performance — the ray cast, which is what made the contract affordable

Three exact optimisations, each verified against brute-force Möller-Trumbore
rather than against a previous run's numbers:

- candidate pruning moved off lists-of-Python-lists onto a C-level sparse
  distance matrix (**4.0×**);
- the ball query is bucketed by triangle radius, so one 6u triangle no longer
  sets the search radius for the whole mesh (a further **1.6×**);
- a ray-line cull before the intersection test — 4% of candidate pairs survive
  it (**2.0×** on the cast).

A region measurement went from **391 ms to ~120 ms**. End-to-end conversion time
is unchanged within run-to-run noise: fit measurement simply is not a
significant fraction of a piece's conversion cost, which is the point — the
contract is free in practice.

### Added — instrumentation, because the chain was speculative

Twelve passes computed against the body, all assumed the garment was in body
space, none asserted it, and nothing between them measured whether a pass
helped. That is how one frame error corrupted all twelve in silence.

- `src/fit_metrics.py` — the canonical in-converter metrics module.
- **Frame precondition**: every phase-2 shape's frame choice is checked and
  recorded when suspect.
- **`ChainGuard`** — the contract described above. An earlier `FitGuard` guarded
  two individual passes and was removed within this same release once the trace
  showed neither ever regressed; the chain verify covers the outcome for a
  quarter of the measurements.
- **`PassTracer`** (`CBBE2UBE_PASS_TRACE=1`) — before/after at every pass
  boundary with shared measurements, so N passes cost N+1 measurements. This is
  what found the groove-smoothing regression, and what showed that guarding each
  pass was the wrong contract.
- **`minimum_push`** — conditional, one-sided targeted push for residual
  exposure. Exits having moved nothing on ~94% of shapes, never moves
  chain-driven (SMP) verts, and reverts on regression.

### Added — physics and follow

- **Bust collider split** and **torso jiggle graft** now default ON, both
  confirmed in game.
- Chest-follow: the material ceiling caps *this pass's graft*, never the
  pass-through, fixing a conflict where the torso graft and the follow pass
  together scored worse (0.325) than either alone (now 0.786).

### Performance

- Canonical body skin and skeleton are cached: **133s → 30s** per armor.
- Ray casting gained an exact range cull and early-out over triangle blocks:
  **6.4h → 2.1h** on a full census.
- The incremental-rebuild floor now includes a hash of every `CBBE2UBE_*`
  environment variable and the NIF-relevant arguments, so changing a setting
  correctly invalidates stale output instead of silently reusing it.

### Fixed — correctness

- Coverage sidecars shipped pre-prune FormIDs, so the merge emitted **zero**
  links. An INI mask hid it. Record objects are now held across
  `prune_unused_masters`.
- `convert_one_armor.py` now takes the **same** path as a batch run. It had
  diverged (different vertex scale, slots resolving to 0), which made
  single-piece validation quietly untrustworthy.
- A diagnostic print can no longer abort a conversion.
- BSA-packed sources resolve in `find_source`.

### Tooling and project

- Report intake (issue templates, diagnostics zip), CI on contributor lanes,
  and CI coverage for Python 3.10 — the runtime the shipped exe actually uses.
- The mod-agnostic tracked-content policy is now enforced by a test rather than
  trusted.

### Known issues

- Butt clipping ~11.6%: needs a physics `can-collide-with-tag` change, not a
  mesh pass.
- Under-bust side (z 80–86) is unresolved; the metric's resolution there is only
  ~10–14 verts.
- Loose robe chests can still bake in roughly +3u of over-inflation from the
  static warp/clearance path.
- The chain-gate on finalize remains reverted.

## 1.2.1 — unreleased

Follow-up to a gap reported in game on a shipped 1.2 mesh. Everything here came
from the pack-wide telemetry 1.2 added.

### Fixed — the only pull-in pass was silently skipped

`conform_to_source_standoff` reels an over-projected garment back onto the body;
every other pass nudges outward. On some pieces it never ran, because **two body
detectors disagreed**. `classify_shapes` identified a shape as the body and
dropped it for the swap; `_is_body_pynifly_shape` then refused the same shape for
carrying fewer than 40 bones — a BodySlide-output inline body only carries the
bones its surviving verts touch, and the affected pieces ship one with 26. The
source-body reference stayed `None` and the gate never opened.

No exception, no warning — the pass was simply absent. It was found only because
the per-pass standoff trace below was built to chase the report.

Measured on the affected piece: strap-line standoff **2.40u → 1.72u**, against a
**1.79u maximum** across 42 shapes where the pass did run. Clipping unchanged at
0.00%, so the gap closed without trading it for skin poking through.

**Scope, measured by a full sweep rather than a sample:** the predicate matches
**103 of 482** phase-2 source pieces (21.4%). Of those, **22 are actually in the
converted output** — the other 81 are overwhelmingly HIMBO/male bodies (77),
which match the predicate but are never converted under the female-only policy.
So 22 pieces across 5 mods need reconverting, dominated by the vanilla-lineage
armours (hide, imperial, stormcloak, draugr, Ysgramor) whose BodySlide output
emits a low-bone inline body.

> An earlier draft of this entry said "rare, not systemic: 42 of 42 armed shapes
> in a 9-mod census already ran the pass". **That was wrong.** The census drew
> nine pieces that all happened to have detectable bodies; a sample that lands
> entirely in one state cannot establish a rate, and it was read as if it had.
> The full sweep replaced it. The 1.2.1 commit message still carries the wrong
> figure and is left alone rather than rewriting pushed history.

The fix is a fallback reached only when the strict detector finds nothing, so it
cannot alter pieces that already work — verified byte-identical on one.

### Added — per-pass STANDOFF trace (`CBBE2UBE_STANDOFF_TRACE=1`, default off)

The existing trace measures clipping and is blind to the opposite defect. This
reports standoff per pass in 3u slabs up the torso, reading the snapshots the
chain already keeps, so it needs no re-conversion. Slabs rather than one window:
a single median over z105–114 read identically for all nine arms of a bisect
because hit density varies ~10× across it.

### Fixed — telemetry that misreported itself

- **`shipped`** added to chain records. `final` is the *rejected* measurement
  when a rollback fires; on the first pack-wide run that read as 174 exposed
  verts against 101 actually shipped, and made a run with zero regressions look
  like 20 shapes ended worse.
- **`path`** added to every record. `nif` is a bare filename and filenames repeat
  across mods — three ship a `cuirassmedium_1.nif` — so a record could not be
  traced to the piece it described.
- The `conform` and chain-verify call sites now **record** their exceptions
  instead of `except Exception: pass`. Both made a failure indistinguishable from
  "nothing to do".
