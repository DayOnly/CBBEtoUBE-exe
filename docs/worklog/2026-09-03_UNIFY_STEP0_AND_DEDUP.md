# Two-path unification: step (0) and the mechanical de-duplication

2026-09-03. Audit F103 step (0) and F102. **No vertex moves**: golden output
identical to the stored baseline at tolerance 0.0u.

## Why this and not the stage-table extraction

Step 6 was gated on step 4, and step 4 is done -- but three unjudged geometry
defaults are riding on the pack currently under test (`phase1_bust_clearance`,
`#family-weight-invariant`, `#phase1-antipoke`), plus an untraced ~3pt butt
coverage drop. The extraction is the change most likely to move geometry
everywhere at once; landing it on top of that queue would make an in-game
problem unattributable and cost the verdicts being earned right now.

Everything here is provably vertex-neutral, so it can land while that testing
continues.

## Step (0): one stage hook, per-path contract made explicit

`_stage` (body-swap) and `_stage_p1` (copy) looked like duplicates and were
NOT. F103's verifier flagged it and it is the whole risk of the step:

* body-swap feeds `_tracer` and `_chain` -- and `_chain` is the ROLLBACK
  checkpoint wrapping seam-weld / coherence / strap / short-edge;
* copy feeds only survival and dump;
* copy returned early on a `None` snapshot, body-swap did not -- so body-swap
  passes `None` through to `chain.checkpoint`.

A naive merge hands the copy path rollback semantics it has never had, on ~78%
of a pack, with no flag and no verdict -- a behaviour change wearing a
refactor's clothes. So `make_stage_hook` takes the recorders as parameters and
`skip_none` as an explicit per-path flag. A path gets exactly what it asks for,
and gaining one has to be written down.

Capture timing was checked, not assumed: `_stage_p1` snapshotted its recorders
as DEFAULT ARGUMENTS while `_stage` read closure variables, so a factory is
only equivalent if nothing rebinds them after the hook is defined. Every
assignment to all six names was enumerated -- copy binds at 3550-3557 and
defines at 3561; body-swap binds at 12278-12324 and defines at 12326. Nothing
rebinds afterwards, so the capture is equivalent.

`tests/test_stage_hook_contract.py` pins this, with a control: injecting
`chain=` into the copy-path call site makes it fail. That guard matters because
the change would be INVISIBLE to the golden pieces -- rollback only fires when a
later verify fails.

## F102: three mechanical duplicates collapsed

Only the ones that are byte-equivalent logic. The fine-animation branch is
NOT touched: its copies genuinely diverge (g2s lift vs raw verts), so merging
it would be a behaviour decision, not a de-duplication.

| duplicate | copies | now |
|---|---|---|
| `_0`/`_1` weight-suffix scan | 3 | `bodyrefs.weight_suffix_of` |
| `meshes` marker walk | 2 | `trigen.armor_relpath_under_meshes` |
| SMP chain-weight skip mask | 3 | `physics.simulated_vert_mask` |

Each helper went to the module that owns its consumer, not to a dumping
ground: the suffix feeds `_find_cbbe_base_body`/`_find_ube_femalebody`, the
marker walk feeds TRI path resolution, the skip mask is built from
`_actor_can_resolve_bone`.

**82 lines out of nif_convert.py.** The helpers carry more text than they
removed, because each one now records what the duplication cost: one of the
marker-walk copies literally said "same logic as phase 2", which is the tell --
a copy that announces it is a copy is one edit from being wrong, and this file
has drifted that way five times.

`simulated_vert_mask` deliberately RAISES rather than returning None: all three
call sites already sit in their own `try` with different fallbacks (two set the
mask to None, one abandons a wider block), so swallowing inside the helper
would silently pick one of them for all three.

## PASS_MAP got more legible, which is the point

Regenerating replaced 3 anonymous `_actor_can_resolve_bone` rows with 10 rows
naming the four shared helpers, guards intact (`PANEL_RIGIDITY`,
`MIXED_CLOTH_CLEARANCE`). The map can now see passes that were buried inside
copy-pasted loops -- an early instalment of what F103 is for.

## A methodology error worth recording

The first golden check of step (0) FAILED on one piece, and the failure was
mine, not the code's: **I edited source while the check was running**, so its
workers imported a moving tree. The re-run on a quiescent tree passed. A
measurement and an edit must not overlap -- the same discipline as not scoring
an A/B against a pack that is still being written.
