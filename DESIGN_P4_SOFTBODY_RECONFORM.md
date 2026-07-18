# P4 — Softbody band re-conform (detailed design)

Pull a too-loose HDT-softbody band IN toward the UBE body when its bundled source body was
a mismatched preset — the GENERAL fix for the Fur Cuirass over-inflation class, for when
P1 (source selection) has no better source to pick.

Status: DESIGN ONLY. HIGHEST risk of the four — edits the CTD-sensitive HDT-SMP path
(cf. CLIPPING_LOG crash C1). Implement only after P1 ships and if source-selection still
leaves gaps. Requires an in-game CTD test on reconvert.

---

## 1. Problem (measured this session)

`Top` (the fur band) is HDT-SMP soft-body cloth. The converter deliberately does NOT warp
softbody verts (moving them disturbs the sim) — it moved `Top` only 0.26u. The band was
authored flush on a bundled `BanditBody1` body with bust +9.88u; injected onto the +5.74u
UBE body it's left standing +1.77u off. The one softbody-aware pass,
`_inflate_cloth_over_bust_butt` (`nif_convert.py:1565`), is PUSH-OUT ONLY — it pushes cloth
out to a fixed clearance where the body POKES through (`poke > 0.1`). When the body is
SMALLER than the band's source body it never pokes, so nothing pulls the loose band IN.
P1 sidesteps this by choosing a better source; P4 fixes the mesh itself when no such source
exists.

## 2. Approach: bidirectional, source-offset-preserving

Generalise `_inflate_cloth_over_bust_butt` so, per band vert, it targets the SAME clearance
the band had over its BUNDLED SOURCE body — pulling IN where it's now looser than that,
pushing OUT where the UBE body pokes (unchanged). The source offset is both the TARGET and
the GATE:

    for each body-band vert b (breast z93-118 front; butt z70-96 back):
        cloth_v      = nearest cloth vert to b
        ube_clear    = (cloth_v - b) . n_ube          # current clearance over UBE
        src_clear    = clearance of that cloth vert over the SOURCE body (precomputed)
        target       = max(MIN_CLEAR, src_clear)      # never below a small floor (anti-poke)
        delta        = target - ube_clear             # <0 => pull in, >0 => push out
    move each cloth vert along the UBE body normal by its (smoothed, capped) delta.

- A tight band (hugged its source body → `src_clear ≈ 0`) → `target ≈ MIN_CLEAR` → pulled
  IN to hug UBE. Fur Cuirass fixed.
- A draping robe (floated off its source body → `src_clear` large) → `target` large → its
  drape is PRESERVED; it is NOT flattened. This gate is what protects robes/cloaks.
- Where the UBE body pokes (`ube_clear < MIN_CLEAR`) → `delta > 0` → push out. Preserves the
  Ancient-Falmer poke-cover the pass was built for.

Body-preserving throughout (only cloth verts move). Push AND pull are the same class of
operation the pass already performs (it already moves softbody verts outward), so pulling
in adds no new partition/palette failure mode.

## 3. The source-body reference (the hard part)

`src_clear` needs the bundled SOURCE body — the body the band was actually conformed to
(`BanditBody1`), not the CBBE reference. Two obstacles, both seen this session:

1. **Detection.** `_is_body_pynifly_shape` rejects `BanditBody1` (1397 verts / 22 bones <
   the 4000-vert / 40-bone gates). Add a low-poly-tolerant, TEXTURE-first detector for THIS
   pass only: a shape with a body-skin diffuse (`_shape_diffuse_is_body_skin`) + full-body
   z-range counts as the source body regardless of poly count. (Same signal P0's
   `_body_provenance` uses; `_shape_diffuse_is_body_skin` is `True` for `BanditBody1`, false
   for every armor shape.) Do NOT lower the global body gate — scope this to the softcloth
   reference only, so the main warp reference is untouched.
2. **Coordinate space.** Bundled bodies are authored SHIFTED (`BanditBody1` z -109..-6 with
   a +120 transform). All of P4's math must run in RENDER space (verts + transform
   translation), and the final displacement applied back in the shape's LOCAL space. This
   is the "flipped-mask / shifted-space costs hours" footgun from memory — measure and test
   in render space explicitly.

Plumb the detected source body (render-space verts + normals) into the pass at the call
site (`nif_convert.py:~11480`). `src_body_v_p2` there is currently the CBBE ref
(`:11086`); add a dedicated `softcloth_src_body` extracted from `src_nif` by the texture
detector, independent of the main-warp reference.

## 4. CTD safety (this is why it's P4, not sooner)

- **No skin/bone changes.** C1 crashed because a pass grafted UBE scale bones / altered a
  softbody partition. P4 is PURE VERTEX REPOSITION — it must not add_bone, re-skin, or touch
  bone weights / partitions. Verify: shape bone list + weights byte-identical before/after.
- **Only the visible band shape.** Never move collider (`_hdt_collider_shape_names`) or
  proxy shapes — moving a collider desyncs the sim (invisible / CTD). Operate solely on the
  draped band geometry; leave `*Col*` / `*Proxy*` / softbody anchors exactly as-is.
- **Cap + smooth** the displacement (reuse `_smooth_push_field`) so no vert jumps — a
  spiky per-vert move on physics cloth is a crinkle at best, a sim blowup at worst.
- **MIN_CLEAR floor** keeps a hair of headroom so a pulled-in band doesn't let the body
  poke during jiggle.
- Default OFF (`CBBE2UBE_SOFTBODY_RECONFORM=1` to enable) until an in-game CTD test on a
  real reconvert passes; then consider default-on.

## 5. Test plan

- Interpreted A/B (`ab_fur.py`-style) from the HDT-SMP Fur source: band standoff +1.77u →
  ~flush WITHOUT swapping source, AND bone list/weights unchanged (no C1 risk).
- A draping SMP robe: drape preserved (src_clear gate holds), not flattened.
- A poke case (UBE bigger than source, e.g. Ancient Falmer): still pushed out to cover.
- Unit: the bidirectional target math (pull-in vs push-out vs preserve-drape) on synthetic
  body+band inputs.
- Suite + goldens (goldens have no softbody → byte-identical).
- **In-game CTD test on reconvert — mandatory** before default-on.

## 6. Relationship to P1

P1 (source selection, low risk) is the PREFERRED fix: pick a source whose body matches UBE,
no physics-path edit. P4 is the fallback for meshes where the ONLY source bundles a
mismatched body (P1 can't help). Ship P1 first; measure how many meshes still gap after it;
only build P4 if that residual is non-trivial. Together they cover the class from both ends
(pick a better source; else re-conform the mesh).

Effort: high. Risk: high (CTD path, coordinate space). Gate hard, test in-game.
