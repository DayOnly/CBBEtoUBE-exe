# F011's "collider regression" (SkirtCol 461 -> 467) is not a defect signal

Read-only investigation, 2026-09-02, while the full reconvert ran.

## What was recorded

`memory/project_current_state.md` (2026-08-24) lists `src_normal_fix` as
"STILL OFF ON PURPOSE ... BREAKS skirt colliders -- 461 -> 467 verts", and
`IN_GAME_BUGS.md` (BUG-13) calls it "a DIFFERENT (real) defect found the same
hour". The 2026-09-01 audit inherited that as one of three things blocking
F011, asking that the delta "reproduce and be explained".

Nothing in either record establishes that a changed vertex COUNT is harm. It
was a number that differed, and it got filed as a defect.

## Why the number cannot mean what it was read to mean

`SkirtCol` is not authored. `_add_skirt_collider_proxy` builds it from the
VISIBLE cloth and hands the vertices to `_cluster_decimate`, a spatial
voxel-grid clustering:

    cell = span / 12.0
    for _ in range(24):
        n = <cells occupied at this cell size>
        if   n > target * 1.15: cell *= 1.12
        elif n < target * 0.85: cell *= 0.92
        else: break

Two consequences, both fatal to the metric:

1. **The loop stops at ANY count within +/-15% of target.** `_SKIRT_PROXY_TARGET`
   is 500, so every value in **[425, 575]** satisfies it equally. 461 and 467
   are both inside. Neither is more "correct" than the other -- the algorithm
   promises "about 500", not a stable number.
2. **The grid is derived from vertex POSITIONS** (`lo = v.min(0)`, `span` from
   the bounding box), so moving the cloth re-lands every cell boundary. And
   `_SRC_NORMAL_FIX` changes conform, which moves the cloth. A different count
   is the EXPECTED result, not a symptom.

## Measured sensitivity

Perturbing a real 8837-vert garment and re-running the same decimator at the
same target (`scratchpad decimator_sensitivity.py`, pure numpy, no convert):

    unperturbed                    477 verts
    gaussian sigma 0.001u      476-478   (span  2)
    gaussian sigma 0.01u       474-481   (span  7)
    gaussian sigma 0.05u       475-484   (span  9)
    gaussian sigma 0.1u        426-499   (span 73)   <--
    gaussian sigma 0.5u        433-504   (span 71)
    rigid translation           477 verts, unchanged at every offset

A 0.1u wobble -- far less than a conform change -- moves the count by up to
**73 vertices**. The recorded "regression" is **6**. It is below the noise floor
of a 0.01u perturbation. (The swing is also non-monotonic with sigma, because
the cell-size loop exits at different iterations: the count is path-dependent,
not a property of the mesh.)

The rigid-translation row is the control: the grid is translation-invariant by
construction (`lo = v.min(0)`), so an unchanged shape gives an unchanged count.
That is what makes the other rows shape sensitivity rather than probe artefact.

## What this does and does not settle

**Settles:** F011's blocker (a) as written. "Explain and clear the 461->467
delta" has no answer to find, because the quantity carries no information about
collider correctness. Do not re-open it, and do not gate anything else on a
generated-proxy vertex count.

**Does NOT settle:** whether `_SRC_NORMAL_FIX` is safe for colliders. That needs
the proxy's actual CONTRACT measured, not its size:

  * `proxy_encloses_chain` = 0 paired against control -- the real BUG-13 defect
    metric (16 of 32 generated proxies enclosed a chain node before the fix);
    tool `passaudit_2026_08_23/proxy_encloses_census.py`.
  * the declared-bone audit -- a collider may only carry XML-DECLARED bones
    (`project_collider_declared_bones`, the collapse class).
  * cloth coverage: does the proxy still represent the cloth it is built from
    (`_SKIRT_PROXY_GAP` / `_SKIRT_PROXY_MIN_UNREPRESENTED` already encode this).

F011's other two blockers are untouched by this: the `#groove-authored-cap`
re-measure with a true target, and the conform retune the constant's own
comment requires.
