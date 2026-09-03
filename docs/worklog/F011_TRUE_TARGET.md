# F011(b): re-measuring `#groove-authored-cap` against a target that is not zero

2026-09-02. Blocker (a) was retired earlier today (`F011_COLLIDER_DELTA.md`:
the 461->467 proxy count carries no information). This is blocker (b).

## Three things established before any convert ran

### 1. The premise holds on a real population, measured with the CODE's detector

`_SRC_NORMAL_FIX` matters only where the source body's stored normals are
unusable. On the body-swap-heavy clothes sample used here:

```
pieces with an inline body (nc._looks_like_inline_body) : 114
  normals ALL-ZERO                                      : 114
  populated                                             :   0
```

Every one. A first pass at this used a name heuristic ("body" in the shape
name) and reported the exact opposite — 12 pieces, 0 zeroed — because it was
scoring shapes that are not the inline body at all. **The code's own detector
is the only one whose answer means anything**; a private re-definition of the
population produced a confident inversion of the result, which is the same
trap as measuring "simulated verts moved" with a hand-rolled chain-weight rule.

### 2. The same function reads this array TWICE and disagrees with itself

Inside `convert_nif_phase2`:

- `nif_convert.py:13513` — `src_body_n_p2` is set from
  `_body_normals_or_compute(...) if _SRC_NORMAL_FIX else getattr(shape, "normals", None)`.
  At defaults that is the **all-zero** array, and it is what
  `conform_to_source_standoff` and `#groove-authored-cap` consume.
- `nif_convert.py:13705` — **the same variable is rebound**, unconditionally,
  to `_body_normals_or_compute(cbbe_body_shape)`, with the comment *"Source
  body normals often zeroed in BodySlide output; compute from tris"*, for the
  chest/abdomen passes.

So one function already treats the recompute as correct and necessary, then
feeds the raw zeros to the passes above it. This is not "an opt-in that has
not been judged"; it is an inconsistency inside a single function, and it is
the strongest rule-1 argument available (`feedback_prevent_dont_patch`).

### 3. What the zeros actually do to the cap

`#groove-authored-cap` (`nif_convert_fitgeom.py:363`) computes the authored
clearance as

```python
s_auth = ((src - sbv[si]) * sbn[si]).sum(axis=1)
cap    = np.maximum(s_in, s_auth)
```

With `sbn` all zero, `s_auth` is **0 everywhere**, so the cap collapses to
`max(s_in, 0)`: groove smoothing may never push a vertex further out than it
already was. Meanwhile conform, fed the same zeros, has read every source
garment as skin-tight and pulled loose drape inward. **Two passes are blind on
one array, and the second one is what stops the first from being undone.**
That is why the cap cannot be judged at defaults: it is being measured in a
world where its own input is a constant.

### Reachability note

`GROOVE_AUTHORED_CAP` is `not _flag("CBBE2UBE_NO_GROOVE_CAP", False)` — default
ON — and `grep -c CBBE2UBE_NO_GROOVE_CAP src/gui_settings.py` is **0**. It has
no Setting row, so no deployed run can switch it and no user can. Whatever the
measurement says, that needs resolving: a default-ON pass with no row is the
`feedback_deployed_build_runs_at_defaults` class.

## The measurement

Four arms, one harness (`exe_parity_convert.py`, the DEPLOYED exe), same mod,
overrides applied after `apply_env` so each arm is the shipped recipe plus one
change:

| arm | `SRC_NORMAL_FIX` | `#groove-authored-cap` |
|---|---|---|
| `ctrl` | off | on (default) |
| `fix` | **on** | on |
| `fix_nocap` | **on** | **off** |
| `nocap` | off | **off** |

`ctrl` vs `nocap` reproduces the historical measurement (cap judged against a
zero target); `fix` vs `fix_nocap` is the same question with a real one.

Scoring is per shape, both weights, on a mask built ONCE from the control arm
(body verts within 8u of the control garment) so a garment that moves cannot
change its own scoring region — and as a boolean mask, because `clip_stats`
and `standoff_stats` call `flatnonzero` internally and silently score the
wrong region when handed indices.

## Results

All four arms: exit 0, 74 NIFs each, through the DEPLOYED exe (git `a6098ec`,
sha `cf9fd6e7`). Arms proved live two ways -- each output's own
`conversion_settings.json` records `src_normal_fix` True/True/False/False, and
the preserved per-arm run log echoes `NO_GROOVE_CAP=1` for both cap arms
(that echo is the ONLY proof available for the cap, which has no Setting row
and so no line in the settings artifact). Geometry differs on 56/74 NIFs for
the normal fix and 58/74 for the cap, so neither arm is a no-op.

### Surface quality: `fold_census`, author control constant at 67 folded

| arm | `SRC_NORMAL_FIX` | cap | folded | inverted |
|---|---|---|---|---|
| `ctrl` | off | on | 6816 | 433 |
| `fix` | **on** | on | **6378** | **238** |
| `fix_nocap` | **on** | off | **6298** | **223** |
| `nocap` | off | off | 6729 | 332 |

Two things fall out, and they point the same way:

1. **The producer fix is a clear surface-quality win on its own.** 6816 -> 6378
   folded (-438, -6.4%) and 433 -> 238 inverted (**-45%**), with the author's
   own 67 folds unchanged across arms (same source, so the control is doing its
   job). This is the fix acting exactly as rule 1 predicts: conform stops
   making bogus pull-ins, so the repair passes downstream have less to undo.

2. **The cap costs folds in BOTH regimes, by about the same amount** -- +87
   with a zero target (`nocap` 6729 -> `ctrl` 6816), +80 with a real one
   (`fix_nocap` 6298 -> `fix` 6378). So the answer to F011(b)'s question, on
   this axis, is that a TRUE TARGET DOES NOT RESCUE THE CAP: its cost is
   roughly independent of whether its input is real.

`pieces with folds` is 31/36 on every arm, so this is a change in fold COUNT
within the same pieces, not pieces crossing in or out of the defect.

<!-- FIT METRICS PENDING -->
