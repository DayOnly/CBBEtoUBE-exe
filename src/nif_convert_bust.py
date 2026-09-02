"""The bust: the body's nipple-weight map, the breast physics-chain index and levers, the chest follow target, the bust-collider split (shape and XML) and the post-write bust-plate follow sync.

Split out of nif_convert.py on 2026-09-01 (split step 6). Imported by name into
nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in
nif_convert are reached through `_nc()` at CALL time, so flags read at
import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still
apply."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import re
import sys

from .atomic_io import (
    atomic_nif_save, atomic_copy, atomic_write_bytes, atomic_tri_save)
from .nif_convert_skinframe import (  # noqa: E402
    _g2s_is_identity, _shape_global_to_skin, _verts_skin_to_world,
    _verts_world_to_skin,
)
from .nif_convert_telemetry import (  # noqa: E402
    _PASS_FAILURES, _PASS_FAILURES_THIS_PIECE, _PASS_EFFECTS,
    _PASS_EFFECTS_THIS_PIECE, _note_pass_failure, _note_pass_effect,
    _piece_pass_effects, pass_effect_summary, _begin_piece_pass_log,
    _piece_pass_failures, pass_failure_summary,
)


def _nc():
    """The monolith, resolved at call time (never at import: circular)."""
    return sys.modules[__package__ + ".nif_convert"]


def _body_nipple_weight(shape) -> "np.ndarray | None":
    """Per-vertex 'nipple weight' from the body's breast-TIP bone weights
    (Breast03 = the apex/tip bone, peaks right at the nipple; Breast02 partial).
    Cleanly localizes the breast front / nipple -- where converted armor needs
    real clearance -- while the sternum, sides and upper chest read ~0 (close
    fit). Returns a (V,) array in ~[0,1], or None if the body carries no breast
    bones (then the bust pass falls back to the flat clearance)."""
    try:
        n = len(shape.verts)
    except Exception:
        return None
    bw = getattr(shape, "bone_weights", None) or {}
    out = np.zeros(n, dtype=np.float64)
    found = False
    for bn, pairs in bw.items():
        b = bn.lower().replace(" ", "")
        mul = next((m for kw, m in _nc().NIPPLE_TIP_BONE_WEIGHTS.items() if kw in b), 0.0)
        if mul <= 0 or pairs is None:
            continue
        found = True
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        for i, w in pl:
            if 0 <= i < n:
                out[i] = max(out[i], float(w) * mul)
    return out if found else None

def _chest_follow_target(shape, n, d, idx_k, vw, body_w, is_chain,
                         src_vw=None) -> "float | None":
    """The follow ratio this shape's chest graft aims for, or None outside ratio
    mode. Geometry sets the amount, the material only caps it.

    ONE ratio for the whole shape, deliberately: applying the per-vert
    requirement directly was MEASURED WORSE than doing nothing (leather cuirass
    4.0% -> 9.5% skin visible at 5u), because neighbouring verts then move by
    different amounts and the surface tears open between them. The requirement
    varies smoothly but the garment deforms as one piece, so the shape takes the
    p90 of what its covered bust verts need.

    `src_vw` (#source-follow) is the AUTHOR's weighting, read from the source
    mesh. It must not be the converted state: our own torso graft runs first and
    would otherwise read as authorship. None falls through to the material
    ceiling, the conservative direction."""
    if not _nc().CHEST_FOLLOW_RATIO:
        return None
    band = _nc()._chest_band(n, d, idx_k, body_w, is_chain)
    if not band:
        return 0.0
    reqs = [_nc()._chest_follow_required(d[i],
                                   sum(w for b, w in body_w[idx_k[i][0]].items()
                                       if b in _nc()._CHEST_JIGGLE_BONE_SET))
            for i in band]
    weighted = None
    if _nc().SOURCE_FOLLOW_CEILING:
        sf = _nc()._shape_bust_follow(src_vw if src_vw is not None else vw,
                                body_w, idx_k, band)
        weighted = None if sf is None else bool(sf >= _nc()._SOURCE_WEIGHTED_MIN)
    ceiling = _nc()._chest_follow_for_shape(shape, source_weighted=weighted)
    return min(ceiling, float(np.percentile(reqs, 90)))

def _bust_split_xml_text(dst_path, nf, src_path=None) -> "str | None":
    """The physics XML the bust split reasons against, resolved ONCE.

    THE ORDER MATTERS AND IS WHY THIS IS SHARED. Bust-split pass 1 runs BEFORE
    the physics finalize installs the output XML, so the DESTINATION pointer
    does not resolve yet -- `_read_source_hdt_xml_text(dst_path)` returns None at
    this point. The authored source copy is the only view available, and it is
    also the correct one: it is the XML the finalize will install.

    Measured, not assumed: reading only the destination made
    `#collider-declared-bones` report `split_col_declared_bones_UNCHECKED` on
    every piece it was meant to repair, i.e. silently do nothing.

    Shared by `_bust_split_candidates` and the declared-bone redirect so the two
    cannot disagree about which XML this piece has -- the same duplicate-list
    mistake `repo_hygiene.should_scan` exists to prevent.
    """
    txt = None
    if src_path is not None and not _nc().CHAIN_TO_SOFTBODY:
        try:
            authored = _nc()._read_source_hdt_xml_disk(Path(src_path))
            if authored is not None:
                txt = Path(authored).read_text(errors="ignore")
        except Exception:
            txt = None
    if txt is None:
        txt = _nc()._read_source_hdt_xml_text(Path(dst_path), nif=nf)
    return txt

def _bust_split_candidates(dst_path, nf, src_path=None) -> list:
    """Garment shapes in `nf` that are their own per-triangle collider AND
    measurably fail to follow the bust -- the split class. Reads the XML the
    physics finalize WILL install (the authored source copy, unless soft-body
    mode keeps the generated one), because that is the view the jiggle graft
    later sees.  [DESIGN: bust collider split]

    Class property, all measured, no name lists:
      * declared as a per-triangle collider by the piece's physics XML,
      * a RENDERED garment (textured, not Hidden) -- collider-only helper
        shapes are excluded by construction,
      * covers the bust band with >= _BUST_SPLIT_MIN_VERTS verts,
      * bust FOLLOW RATIO (breast weight vs the body underneath, verts within
        _BUST_SPLIT_PROX) below _BUST_SPLIT_FOLLOW_FLOOR -- weight, never bone
        presence: a garment with authored bust follow is left alone,
      * its name appears in the XML ONLY as the per-triangle decl -- a name
        also referenced elsewhere (constraints, collision pairs) is a
        structure this fix has not been validated on, so it is skipped.
    """
    if not (_nc().BUST_COLLIDER_SPLIT and _nc().TORSO_JIGGLE_TRANSFER
            and _nc().TRANSFER_BODY_JIGGLE):
        return []
    txt = _bust_split_xml_text(dst_path, nf, src_path)
    if txt is None:
        txt = _nc()._read_source_hdt_xml_text(Path(dst_path), nif=nf)
    if not txt:
        return []
    colliders = set(re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt))
    if not colliders:
        return []
    weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
    ref = _nc()._body_conform_ref(weight)
    if ref is None:
        return []           # no body to measure follow against -> no split
    _Vb, body_w, _bb, tree = ref
    names = {s.name for s in nf.shapes}
    out = []
    for s in nf.shapes:
        name = s.name or ""
        if name not in colliders:
            continue
        if (name.endswith(_nc()._BUST_SPLIT_COL_SUFFIX)
                or (name + _nc()._BUST_SPLIT_COL_SUFFIX) in names):
            continue        # already split (or is itself a clone)
        if _nc()._is_inline_body_name(name) or name == "BaseShape":
            continue        # never the body
        if int(getattr(s, "flags", 0) or 0) & 0x1:
            continue        # Hidden -> a collider helper, not a garment
        if not any(v for v in (s.textures or {}).values()):
            continue        # textureless -> not a rendered garment
        # the name must have no XML role beyond the per-triangle decl(s)
        decl_uses = len(re.findall(
            r'<per-triangle-shape\s+name="' + re.escape(name) + r'"', txt))
        if txt.count(f'"{name}"') != decl_uses:
            continue
        try:
            g2s = _shape_global_to_skin(s)
            V = _verts_skin_to_world(np.asarray(s.verts, np.float64), g2s)
        except Exception:
            continue
        z = V[:, 2]
        band = np.flatnonzero((z >= _nc()._CHEST_Z_LO) & (z <= _nc()._CHEST_Z_HI))
        if len(band) < _nc()._BUST_SPLIT_MIN_VERTS:
            continue
        d, idx = tree.query(V[band])
        keep = d < _nc()._BUST_SPLIT_PROX
        if int(keep.sum()) < _nc()._BUST_SPLIT_MIN_VERTS:
            continue
        bw = s.bone_weights or {}
        per_vert = np.zeros(len(V), np.float64)
        for b, pairs in bw.items():
            if _nc()._BREAST_BONE_RE.search(b):
                for vi, w in pairs:
                    iv = int(vi)
                    if 0 <= iv < len(V):
                        per_vert[iv] += float(w)
        under = float(np.mean([
            sum(w for b, w in body_w[int(j)].items()
                if _nc()._BREAST_BONE_RE.search(b))
            for j in idx[keep]]))
        if under <= 1e-6:
            continue        # body's bust is not here -> nothing to follow
        follow = float(per_vert[band][keep].mean()) / under
        if follow < _nc()._BUST_SPLIT_FOLLOW_FLOOR:
            out.append(name)
    return out

def _split_bust_collider_shape(dst_path, src_path=None) -> int:
    """PASS 1 of the bust collider split: add the hidden collider clone.

    ORDER-CRITICAL: runs BEFORE _finalize_hdt_physics, so the clone exists when
    that pass validates/hardens the XML against the NIF -- and the clone is
    added IN PLACE on the loaded NifFile (the same mechanism the finalize uses
    for re-imported proxies). Rebuilding a NIF from its shapes drops ALL extra
    data (BODYTRI + the physics link -- in game: "ignores morphs, body reverts
    to its _0 version"), which is why this is not a rebuild.

    All-or-nothing with a byte-restore: after the save the file is reloaded
    and every pre-existing shape and extra-data name must survive alongside
    the clone(s); on any loss the original bytes are written back. Returns
    clones added (0 = untouched or restored)."""
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0  # effect-shader/glow NIF: a reload+re-save corrupts its
                  # controller -> CTD. Every sibling in-place pass carries this
                  # bail; the audit found this one missing it.
    cands = _bust_split_candidates(dst_path, nf, src_path=src_path)
    if not cands:
        return 0
    try:
        backup = Path(dst_path).read_bytes()
    except Exception:
        return 0

    def _all_extra(nf_):
        # BODYTRI lives on its CARRIER SHAPE, not the root -- snapshot both, or
        # the morph invariant is checked against the wrong place.
        out = {(None, getattr(ed, "name", None))
               for ed in nf_.rootNode.extra_data()}
        for s_ in nf_.shapes:
            try:
                out |= {(s_.name, getattr(ed, "name", None))
                        for ed in s_.extra_data()}
            except Exception:
                pass
        return out

    pre_extra = _all_extra(nf)
    pre_shapes = {s.name for s in nf.shapes}
    # Second instance of the same file as the copy SOURCE: _copy_shape reads
    # from one NifFile and authors into another; self-copy within one handle
    # is not a validated path.
    try:
        nf_src = pyn.NifFile(filepath=str(dst_path))
        src_by = {s.name: s for s in nf_src.shapes}
    except Exception:
        return 0
    # #collider-declared-bones. Declarations come from the SAME resolution the
    # candidate scan uses -- see `_bust_split_xml_text` for why the destination
    # alone is not enough HERE (pass 1 precedes the finalize that writes it),
    # even though `_audit_registered_shape_declared_bones`, which runs at the
    # very END of the tail, correctly reads the destination.
    _decl_txt = (_bust_split_xml_text(dst_path, nf, src_path)
                 if _nc().SPLIT_COL_DECLARED_BONES else None)
    _declared = set(re.findall(r'<bone\s+name="([^"]+)"', _decl_txt or ""))

    def _redirect_undeclared(sh):
        """`(override_skin | None, declined_bone | None)` for a clone of `sh`.

        `(None, None)` means nothing needed redirecting, or the XML could not be
        read -- clone exactly as before. A bone with no declared ancestor ON THIS
        SHAPE returns it as `declined`, and the caller must not create the clone:
        shipping it re-creates the free-fall this exists to prevent.
        """
        bw = getattr(sh, "bone_weights", None) or {}
        if not bw:
            return None, None
        if not _declared:
            # CANNOT CHECK IS NOT THE SAME AS NOTHING TO REPORT -- the same rule
            # `_audit_registered_shape_declared_bones` follows. Silence here
            # would look exactly like "every bone was already declared".
            _note_pass_failure(
                "split_col_declared_bones_UNCHECKED", RuntimeError(
                    f"{Path(dst_path).name}: cloning {sh.name!r} into a "
                    f"registered collider but no XML bone declarations could be "
                    f"read, so the declared-bone invariant was NOT verified"),
                dst_path)
            return None, None
        avail = set(bw.keys())
        if not (avail - _declared):
            return None, None                  # every bone already declared
        acc: "dict[str, dict[int, float]]" = {}
        for bname, pairs in bw.items():
            rows = (pairs.tolist() if hasattr(pairs, "tolist") else pairs)
            tgt = bname
            if bname not in _declared:
                tgt = _nc()._nearest_declared_ancestor(bname, _declared, avail)
                if tgt is None:
                    return None, bname
            # ACCUMULATE per vertex -- two redirected bones can land on the same
            # ancestor and the second must not overwrite the first.
            m = acc.setdefault(tgt, {})
            for vi, w in rows:
                vi = int(vi)
                m[vi] = m.get(vi, 0.0) + float(w)
        bones, xforms, weights = [], {}, {}
        for bname, m in acc.items():
            try:
                stb = sh.get_shape_skin_to_bone(bname)
            except Exception:
                return None, bname             # unreadable STB -> decline
            if stb is None:
                return None, bname
            bones.append(bname)
            xforms[bname] = stb
            weights[bname] = sorted(m.items())
        return {"bones": bones, "xforms": xforms, "weights": weights}, None

    added = []
    for name in cands:
        gsh = src_by.get(name)
        if gsh is None:
            continue
        _skin, _declined = _redirect_undeclared(gsh)
        if _declined is not None:
            print(f"    [bust-split] {Path(dst_path).name}: DECLINED {name} -- "
                  f"no XML-declared ancestor for {_declined!r}; a registered "
                  f"collider carrying it cannot be resolved by FSMP and the "
                  f"piece free-falls", file=sys.stderr)
            # A DECLINE is the loudest thing this change does -- the piece loses
            # a collider it used to ship -- so it is recorded even though
            # nothing was written. #change-attribution
            _note_pass_effect("#collider-declared-bones",
                              f"DECLINED {name} ({_declined})", dst_path)
            continue
        if _skin is not None:
            _note_pass_effect("#collider-declared-bones",
                              f"redirected undeclared bone(s) on {name}Col",
                              dst_path)
        clone = _nc()._copy_shape(gsh, nf, preserve_authored_skin=True,
                            override_skin=_skin)
        if clone is None:
            return 0        # nothing saved yet -> file untouched
        try:
            clone.name = name + _nc()._BUST_SPLIT_COL_SUFFIX
        except Exception:
            return 0        # cannot rename -> abort before any save
        # Hide it and strip textures. Without this the "collider" ships as a
        # VISIBLE duplicate z-fighting the garment. flags=15 matches the
        # hand-authored hidden colliders (HDT reads geometry; render skips).
        try:
            pr = getattr(clone, "properties", None)
            if pr is not None and hasattr(pr, "flags"):
                pr.flags = _nc()._BUST_SPLIT_HIDDEN_FLAGS
            if hasattr(clone, "flags"):
                clone.flags = _nc()._BUST_SPLIT_HIDDEN_FLAGS
        except Exception:
            return 0        # a visible duplicate must never ship
        for slot in list((clone.textures or {}).keys()):
            try:
                clone.set_texture(slot, "")
            except Exception:
                pass
        added.append(name)
    if not added:
        return 0
    _nc()._hide_virtual_body(nf)
    try:
        atomic_nif_save(nf, dst_path)
    except Exception as _se:
        # See _match_rigid_leg_bend_to_body: `return 0` already means "nothing
        # qualified", so a swallowed save reads as a clean no-op.
        _note_pass_failure("_split_bust_collider_shape/save", _se, dst_path)
        return 0
    ok = False
    try:
        nf2 = pyn.NifFile(filepath=str(dst_path))
        post_extra = _all_extra(nf2)
        post_shapes = {s.name for s in nf2.shapes}
        ok = (pre_extra <= post_extra and pre_shapes <= post_shapes
              and all((n + _nc()._BUST_SPLIT_COL_SUFFIX) in post_shapes
                      for n in added))
    except Exception:
        ok = False
    if not ok:
        try:
            atomic_write_bytes(dst_path, backup)
        except Exception:
            pass
        print(f"  WARN: bust-collider split on {Path(dst_path).name} lost "
              f"shapes/extra-data -- RESTORED original, split skipped",
              file=sys.stderr)
        return 0
    for n in added:
        print(f"    [bust-split] {Path(dst_path).name}: {n} -> "
              f"{n}{_nc()._BUST_SPLIT_COL_SUFFIX} (hidden collider clone)")
    return len(added)

def _split_bust_collider_xml(dst_path) -> int:
    """PASS 2 of the bust collider split: repoint the physics XML at the clone.

    ORDER-CRITICAL: runs AFTER _finalize_hdt_physics -- which OVERWRITES the
    on-disk XML with the authored source copy, silently undoing any earlier
    rewrite (that trap restored the exact configuration that tore breasts off)
    -- and BEFORE _transfer_body_jiggle_to_fitted, which reads this XML to
    decide what is a collider. The split / morph / physics invariants are
    gated TOGETHER: the decl is rewritten only when the garment AND its clone
    are both in the NIF (clones only survive pass 1's restore-on-loss check)
    and the NIF still carries its physics extra-data. Returns decls rewritten.
    """
    if not (_nc().BUST_COLLIDER_SPLIT and _nc().TORSO_JIGGLE_TRANSFER
            and _nc().TRANSFER_BODY_JIGGLE):
        return 0
    p = Path(dst_path)
    stem = p.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    xml = p.parent / f"{stem}.xml"
    if not xml.is_file():
        return 0
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(p))
        shapes = {s.name for s in nf.shapes}
        extra = {getattr(ed, "name", None) for ed in nf.rootNode.extra_data()}
    except Exception:
        return 0
    if "HDT Skinned Mesh Physics Object" not in extra:
        return 0            # physics invariant: no link, nothing to repoint
    # Byte-preserving text handling: utf-8 strict first, latin-1 fallback
    # (latin-1 round-trips every byte; the names touched are ASCII). Never a
    # replacing decode -- that manufactures U+FFFD mojibake on re-encode.
    raw = xml.read_bytes()
    codec = "utf-8"
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        codec = "latin-1"
        txt = raw.decode("latin-1")
    n_rw = 0
    for name in set(re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt)):
        if name.endswith(_nc()._BUST_SPLIT_COL_SUFFIX):
            continue
        col = name + _nc()._BUST_SPLIT_COL_SUFFIX
        if col not in shapes or name not in shapes:
            continue        # split invariant: garment AND clone both present
        txt, n = re.subn(
            r'(<per-triangle-shape\s+name=")' + re.escape(name) + r'(")',
            r'\g<1>' + col.replace("\\", "\\\\") + r'\g<2>', txt)
        n_rw += n
    if n_rw:
        try:
            atomic_write_bytes(xml, txt.encode(codec))
        except Exception:
            return 0
        print(f"    [bust-split] {xml.name}: {n_rw} per-triangle decl(s) "
              f"repointed at the hidden clone")
    return n_rw

def _breast_chain_index(bone):
    """-> (side, chain index) for a breast CHAIN bone, else None.

    Deliberately narrower than the `"breast" in name` test the rest of this pass
    uses: only the numbered chain may be transplanted onto, so any other
    breast-named bone keeps the plain rescale and can never lose its weight to a
    relabel it was not measured for.
    """
    lb = bone.lower()
    side = "L" if lb.startswith("l ") else ("R" if lb.startswith("r ") else None)
    if side is None:
        return None
    for i, c in enumerate(_nc().BUST_PLATE_CHAIN):
        if c.lower() in lb:
            return side, i
    return None

def _breast_chain_levers(nf):
    """Per chain bone, how far along the chain it sits -- a travel proxy.

    Motion under a joint chain COMPOUNDS, so a bone's distance from the chain
    ROOT stands in for how far a vertex bound to it moves. Measured off the
    BODY's own weighting (the authored chain) as a weight-averaged centroid in
    +y, which is the axis the bust travels on.

    Returns a per-chain-index list, or [] when the body or the chain is missing
    -- the caller then skips the transplant rather than inventing a geometry.
    """
    body = next((s for s in nf.shapes if s.name == "BaseShape"), None)
    if body is None:
        return []
    try:
        bv = np.asarray(body.verts, dtype=np.float64)
        bw = body.bone_weights or {}
    except Exception:
        return []
    if bv.size == 0:
        return []
    cen = {}
    for b, pairs in bw.items():
        ci = _breast_chain_index(b)
        if ci is None or not pairs:
            continue
        idx = np.array([int(v) for v, _w in pairs])
        w = np.array([float(_w) for _v, _w in pairs], dtype=np.float64)
        if w.sum() <= 0 or idx.size == 0 or int(idx.max()) >= len(bv):
            continue
        cen[b] = float(np.average(bv[idx, 1], weights=w))
    out = []
    for i in range(len(_nc().BUST_PLATE_CHAIN)):
        vals = []
        for side in ("L", "R"):
            root = cen.get(f"{side} {_nc().BUST_PLATE_CHAIN[0]}")
            y = cen.get(f"{side} {_nc().BUST_PLATE_CHAIN[i]}")
            if root is not None and y is not None:
                vals.append(y - root)
        if not vals:
            return []          # incomplete chain -> no transplant
        out.append(float(np.mean(vals)))
    return out

def _sync_bust_plate_follow_postwrite(dst_path) -> int:
    """#bust-plate-sync. Raise a rigid bust PLATE's breast-family follow up to the
    jiggle CLOTH stacked beneath it, so the cloth stops swinging out through the
    plate under breast physics. Returns the number of receiver verts raised.

    POST-WRITE by necessity: the breast follow split is only final after the
    family / full-vector weight matches have run on `dst_path`, and whichever pass
    touches a bone LAST wins it. Reading the FINAL NIF here, the authority (the
    highest band-follow layer) is the inner cloth and the receiver is the outer
    plate -- exactly the order we want: pull the plate UP to the cloth's jiggle,
    never the cloth down to the plate's stiffness. (A pre-write attempt on source
    weights picked the authority backwards AND was overwritten downstream.)

    Conservative, in the spirit of _match_limb_motion_to_body:
      * PUSH-UP ONLY -- never lowers a layer's follow; a plate already tracking the
        cloth is left alone.
      * NO add_bone BY DEFAULT -- only rescales the breast-vs-non-breast SHARE
        among the bones a vertex ALREADY carries. So it can never introduce a
        physics/chain/SMP bone onto a rigid plate (the equip-CTD guard is
        structural, not a filter), it never moves a vertex (bind pose
        byte-identical), and the relative proportions WITHIN the non-breast bones
        are preserved, so body-follow is only scaled, never re-based (the trap
        that gutted the shared-basis fix in #layer-follow-divergence).
        BUST_PLATE_CHAIN_TRANSPLANT (opt-in) is the one thing that relaxes this:
        it gives the plate the chain bone whose TRAVEL matches the cloth, which
        needs add_bone and so trades that structural guard for a judgement. It
        still never raises a vertex's influence COUNT and never changes a
        vertex's total weight -- it relabels which chain bone holds it.
      * admission by COVERAGE, not weight -- a genuine plate HAS breast bones but
        low follow, so only a layer that MUTUALLY overlaps a higher-follow breast
        layer is admitted; an ornament grazing the chest overlaps one-way at most.
      * skips colliders / soft-body / fx shapes, per the standing rule that a skin
        pass leaves authored physics geometry alone.
    """
    if not _nc().BUST_PLATE_SYNC:
        return 0
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0  # effect-shader NIF: a reload+re-save corrupts its controller -> CTD
    # FAIL CLOSED. These name the authored physics geometry this pass must not
    # touch, so an empty set on failure is not a safe default -- it would silently
    # drop the guard below and let a collider / soft-body shape be reweighted,
    # the class this project has traced to equip CTDs and cloth collapse. A
    # lookup that RAISED tells us nothing about the piece, so skip it entirely
    # and RECORD why (a bare fallback made a protection-less run read as clean).
    try:
        collider_names = _nc()._hdt_collider_shape_names(dst_path, nif=nf)
        softbody_names = _nc()._hdt_softbody_shape_names(dst_path, nif=nf)
    except Exception as _pe:
        _note_pass_failure(
            "_sync_bust_plate_follow_postwrite/physics-names", _pe, dst_path)
        print(f"  WARN: bust-plate follow skipped on {Path(dst_path).name}: "
              f"could not resolve the physics shape names ({_pe!r})",
              file=sys.stderr)
        return 0
    # NO MORPH-TRI SKIP HERE, DELIBERATELY -- and this is a real divergence from
    # every sibling weight pass, so it is argued rather than assumed. Those passes
    # (`_match_limb_motion_to_body` and friends) skip a shape driven by its own
    # source morph TRI because they RE-SHARE LIMB MASS: they move mass between
    # arm/leg/spine families, which changes how the shape answers a joint
    # ROTATION its TRI was authored against. MEASURED CONSEQUENCE OF ADOPTING IT
    # HERE: on the piece this pass was built and in-game-verified for, ALL of
    # `chest_plate`, `top`, `corset`, `belts` are morph-TRI driven, so the gate
    # admits nothing and the plate stays at its unfixed 0.095 follow -- and the
    # pack is ~88% morph-TRI gated (see the pre-reconvert censuses), so the skip
    # does not make this pass safe, it deletes it. The in-game verdict on that
    # fully morph-TRI piece was that plate and cloth now move together.
    # KNOWN LIMIT: that verdict covers JIGGLE. Whether a rewritten plate answers
    # BODY SLIDERS exactly as before is the untested axis -- a morph A/B, not a
    # skip, is what would settle it. #bust-plate-sync
    try:
        from scipy.spatial import cKDTree

        # --- gather candidate bust layers: (shape, box_verts, box_idx,
        #     band_follow, per-vert [breast_w, total_w] rows) ----------------
        cand = []
        for s in nf.shapes:
            if s.name == "BaseShape" or s.name in _nc().RESKIN_SKIP_NAMES:
                continue
            if s.name in collider_names or s.name in softbody_names:
                continue
            bw = s.bone_weights or {}
            if not any("breast" in b.lower() for b in bw):
                continue
            v = np.asarray(s.verts, dtype=np.float64)
            if v.size == 0:
                continue
            box = ((v[:, 2] >= _nc().CHEST_SYNC_Z_MIN) & (v[:, 2] <= _nc().CHEST_SYNC_Z_MAX)
                   & (np.abs(v[:, 0]) <= _nc().CHEST_SYNC_X_BOUND)
                   & (v[:, 1] >= _nc().CHEST_SYNC_Y_MIN))
            if int(box.sum()) < 5:
                continue
            box_idx = np.where(box)[0]
            rows = {}  # vert idx -> [breast_w, total_w] over the cleavage box
            # vert idx -> per-chain-bone weight, built ONLY for the transplant so
            # the default path stays exactly as it shipped.
            chain_rows = {} if _nc().BUST_PLATE_CHAIN_TRANSPLANT else None
            box_set = set(int(i) for i in box_idx)
            for b, prs in bw.items():
                isbr = "breast" in b.lower()
                ci = (_breast_chain_index(b)
                      if _nc().BUST_PLATE_CHAIN_TRANSPLANT else None)
                for vi, w in prs:
                    if w <= 0.0:
                        continue
                    ivi = int(vi)
                    if ivi not in box_set:
                        continue
                    r = rows.get(ivi)
                    if r is None:
                        r = rows[ivi] = [0.0, 0.0]
                    r[1] += w
                    if isbr:
                        r[0] += w
                    if ci is not None:
                        cr = chain_rows.get(ivi)
                        if cr is None:
                            cr = chain_rows[ivi] = [0.0] * len(_nc().BUST_PLATE_CHAIN)
                        cr[ci[1]] += w
            # band-follow (z 88-104, front) ranks authority
            band = (box & (v[:, 2] >= _nc().BUST_PLATE_BAND_Z_LO)
                    & (v[:, 2] <= _nc().BUST_PLATE_BAND_Z_HI)
                    & (v[:, 1] > _nc().BUST_PLATE_BAND_Y_MIN))
            band_bw = band_tw = 0.0
            for ivi in (int(i) for i in np.where(band)[0]):
                r = rows.get(ivi)
                if r:
                    band_tw += r[1]
                    band_bw += r[0]
            band_follow = (band_bw / band_tw) if band_tw > 0 else 0.0
            cand.append((s, v[box_idx], box_idx, band_follow, rows, chain_rows))
        if len(cand) < 2:
            return 0

        # --- admit a stacked group by MUTUAL overlap over the cleavage box ---
        cand.sort(key=lambda c: -c[3])          # highest band follow = authority
        auth = cand[0]
        atree = cKDTree(auth[1])
        group = [auth]
        for c in cand[1:]:
            d_ca, _ = atree.query(c[1])
            d_ac, _ = cKDTree(c[1]).query(auth[1])
            ov = min(float((d_ca < _nc().CHEST_SYNC_DISTANCE).mean()),
                     float((d_ac < _nc().CHEST_SYNC_DISTANCE).mean()))
            if ov >= _nc().BUST_PLATE_SYNC_OVERLAP:
                group.append(c)
        if len(group) < 2:
            return 0

        _as, _abv, auth_idx, auth_follow, auth_rows, auth_chain = group[0]
        # authority's LOCAL breast fraction, 1:1 with its cleavage-box verts
        auth_frac = np.array([
            (auth_rows[int(vi)][0] / auth_rows[int(vi)][1])
            if (int(vi) in auth_rows and auth_rows[int(vi)][1] > 0) else 0.0
            for vi in auth_idx], dtype=np.float64)

        # --- transplant geometry: the authority's effective LEVER per vert ----
        # How far the cloth actually travels here, as the chain-weighted mean of
        # the joint levers. This is what the plate's single chain bone is chosen
        # to match. Empty levers (no body / broken chain) disable the transplant
        # rather than guessing.
        levers = (_breast_chain_levers(nf)
                  if _nc().BUST_PLATE_CHAIN_TRANSPLANT else [])
        allowed = list(range(min(_nc().BUST_PLATE_CHAIN_DEPTH, len(levers))))
        auth_lever = None
        if levers and allowed:
            auth_lever = np.full(len(auth_idx), np.nan, dtype=np.float64)
            lv = np.asarray(levers, dtype=np.float64)
            for k, vi in enumerate(auth_idx):
                cr = (auth_chain or {}).get(int(vi))
                if not cr:
                    continue
                tot = float(sum(cr))
                if tot > 0:
                    auth_lever[k] = float(np.dot(np.asarray(cr) / tot, lv))

        # --- PLAN EVERY RECEIVER FIRST, AND ONLY THEN WRITE ------------------
        # ALL-OR-NOTHING PER PIECE. Raising SOME of a stack is not a partial fix,
        # it is a NEW divergence: the layers left behind now disagree with the
        # ones that moved, which is the very defect this pass exists to remove.
        # And a shape this pass can only PARTIALLY reach tears in half -- it can
        # raise a vertex's breast share only among bones that vertex ALREADY has
        # (no add_bone, the CTD guard), so a vertex carrying no breast bone can
        # never be raised. REPORTED IN GAME on a robe whose `TopLeather` carries
        # R-side breast bones only: the pass raised its LEFT half 0.000 -> 0.366
        # and left the RIGHT half at 0.000, and the garment split apart at the
        # bust. That plated top hid this -- only 1.5% of its plate verts lack a
        # breast bone, and they are scattered rather than a whole side.
        # So: if any receiver cannot be brought along essentially in full, this
        # piece is not safely fixable and NOTHING is written. #bust-plate-sync
        plans = []
        for (s, box_v, box_idx, follow, rows, chain_rows) in group[1:]:
            if auth_follow - follow < _nc().BUST_PLATE_SYNC_MIN_GAP:
                # Too close to be the plate-vs-cloth divergence this pass exists
                # for -- rewriting thousands of weights to chase follow noise
                # only risks an authored difference. Also covers `follow >=
                # auth_follow` (the authority is the max, but guard anyway).
                continue
            dists, nn = atree.query(box_v, k=1,
                                    distance_upper_bound=_nc().CHEST_SYNC_DISTANCE)
            # per-vert breast/non-breast rescale factors (push-up only)
            scale_br = {}
            scale_nb = {}
            # vert idx -> chain index its breast weight should be carried on
            chain_tgt = {}
            n_need = 0        # verts that WANT raising (the authority is higher)
            n_blocked = 0     # ... of those, the ones with no breast bone to raise
            for li, (d, ai) in enumerate(zip(dists, nn)):
                if not np.isfinite(d) or d > _nc().CHEST_SYNC_DISTANCE:
                    continue
                if ai < 0 or ai >= len(auth_frac):
                    continue
                ivi = int(box_idx[li])
                r = rows.get(ivi)
                if r is None or r[1] <= 0:
                    continue
                B, T = r[0], r[1]
                cur = B / T
                tgt = float(auth_frac[ai])
                # CHAIN TARGET FIRST, and for EVERY vert carrying chain weight --
                # including the ones the push-up leaves alone. The bone-shape
                # defect does not depend on whether the FRACTION needs raising,
                # and transplanting only the raised verts would leave a lever
                # STEP through the middle of the plate: the same blocked-REGION
                # shape that split a robe in game, arriving by a different route.
                if auth_lever is not None and chain_rows is not None:
                    cr = chain_rows.get(ivi)
                    al = float(auth_lever[ai])
                    if cr and sum(cr) > 0 and np.isfinite(al):
                        best = min(allowed,
                                   key=lambda ci: abs(levers[ci] - al))
                        chain_tgt[ivi] = best
                if tgt <= cur + 1e-4:
                    continue  # already tracks the cloth here -> leave it
                n_need += 1
                if B <= 0.0:
                    # No breast bone on this vertex: unreachable without add_bone.
                    # COUNTED, not silently skipped -- the count is the coverage
                    # test below, and skipping quietly is what tore the robe.
                    n_blocked += 1
                    continue
                N = T - B
                scale_br[ivi] = (tgt * T) / B
                scale_nb[ivi] = ((1.0 - tgt) * T / N) if N > 1e-12 else 0.0
            if not scale_br and not chain_tgt:
                continue
            cover = (n_need - n_blocked) / float(max(n_need, 1))
            if cover < _nc().BUST_PLATE_SYNC_MIN_COVER:
                print(f"  bust-plate follow: {Path(dst_path).name} left alone -- "
                      f"{s.name!r} is only {cover*100:.0f}% reachable "
                      f"({n_blocked} of {n_need} verts carry no breast bone), and "
                      f"a partly-raised layer splits apart", file=sys.stderr)
                return 0
            plans.append((s, scale_br, scale_nb, chain_tgt))

        total = 0
        dirty = False
        for (s, scale_br, scale_nb, chain_tgt) in plans:
            bw = s.bone_weights or {}
            bone_list = list(s.bone_names or list(bw.keys()))
            # --- transplant: resolve the bones to add, and their STB donors ---
            # A bone added without a correct skin-to-bone transform spikes the
            # mesh, so a bone with NO donor in this NIF disables the transplant
            # for the shape rather than being added blind.
            want, donor_stb = [], {}
            if chain_tgt:
                want = sorted({f"{side} {_nc().BUST_PLATE_CHAIN[ci]}"
                               for ci in set(chain_tgt.values())
                               for side in ("L", "R")} - set(bone_list))
                for bone in want:
                    for other in nf.shapes:
                        if other.name == s.name:
                            continue
                        if bone not in (other.bone_names or []):
                            continue
                        try:
                            st = other.get_shape_skin_to_bone(bone)
                        except Exception:
                            st = None
                        if st is not None:
                            donor_stb[bone] = st
                            break
                missing = [b for b in want if b not in donor_stb]
                if missing:
                    print(f"  bust-plate transplant: {s.name!r} left on the "
                          f"chain base -- no skin-to-bone donor in this NIF for "
                          f"{missing}", file=sys.stderr)
                    chain_tgt, want = {}, []
            # STBs: setShapeWeights can reset them, so save every bone and restore
            # afterwards. If any can't be read, skip -- an identity STB = spike.
            saved_stb = {}
            ok = True
            for b in bone_list:
                try:
                    st = s.get_shape_skin_to_bone(b)
                except Exception:
                    st = None
                if st is None:
                    ok = False
                    break
                saved_stb[b] = st
            if not ok:
                continue
            if want:
                # add_bone RESETS the shape's whole bone table, so per its own
                # docstring: add EVERY bone first, then set EVERY transform, then
                # weights. And `s.bone_names` is CACHED -- it will not show what
                # was just added -- so EXTEND the list already held. Re-reading it
                # here is exactly how these bones once shipped at zero weight.
                try:
                    for bone in want:
                        s.add_bone(bone)
                    for bone in want:
                        s.set_skin_to_bone_xform(bone, donor_stb[bone])
                    for b, st in saved_stb.items():
                        s.set_skin_to_bone_xform(b, st)
                except Exception as _ae:
                    print(f"  WARN: bust-plate transplant could not add "
                          f"{want} to {s.name!r} ({_ae!r}) -- scale only",
                          file=sys.stderr)
                    _note_pass_failure("bust-plate transplant/add_bone", _ae)
                    chain_tgt = {}
                else:
                    saved_stb.update(donor_stb)
                    bone_list = bone_list + want
            # Per-vert breast total after the push-up, re-homed onto the chain
            # bone whose travel matches the cloth. Side is PRESERVED (an L vert's
            # weight stays on an L bone) and the per-vert total is untouched, so
            # this relabels rather than redistributes: one chain bone replaces
            # another, the influence count never rises, and nothing is evicted.
            moved = {}          # bone -> {vert: weight}
            for b in bone_list:
                ci = _breast_chain_index(b) if chain_tgt else None
                if ci is None:
                    continue
                for vi, w in bw.get(b, []):
                    ivi = int(vi)
                    if ivi not in chain_tgt or w <= 0.0:
                        continue
                    tb = f"{ci[0]} {_nc().BUST_PLATE_CHAIN[chain_tgt[ivi]]}"
                    d = moved.setdefault(tb, {})
                    d[ivi] = d.get(ivi, 0.0) + float(w) * scale_br.get(ivi, 1.0)
            # Full per-bone rewrite: unchanged verts keep their weight, the raised
            # verts get breast scaled up / non-breast scaled down.
            try:
                for b in bone_list:
                    isbr = "breast" in b.lower()
                    ci = _breast_chain_index(b) if chain_tgt else None
                    outp = []
                    if ci is not None:
                        # transplanted verts are re-emitted from `moved` (possibly
                        # onto this same bone); everything else is untouched.
                        prev = {int(vi): float(w) for vi, w in bw.get(b, [])}
                        mine = moved.get(b, {})
                        for ivi, w in prev.items():
                            if ivi not in chain_tgt:
                                if w > _nc()._WRITE_MIN:
                                    outp.append((ivi, w))
                            elif ivi not in mine:
                                # This bone is GIVING THIS VERTEX UP, and that has
                                # to be said out loud: setShapeWeights UPDATES the
                                # pairs it is handed and leaves every other vertex
                                # of that bone alone, so omitting one does NOT
                                # remove it. Measured when this wrote the new bone
                                # and merely skipped the old: the weight landed on
                                # BOTH, 5355 verts gained an influence and sums
                                # went 1.000 -> 1.300. Every other weight pass here
                                # rewrites VALUES and never has to remove a vertex
                                # from a bone, which is why nothing hit this first.
                                outp.append((ivi, 0.0))
                        for ivi, w in mine.items():
                            if w > _nc()._WRITE_MIN:
                                outp.append((ivi, w))
                        outp.sort()
                        # ALWAYS write, even empty: a chain bone that gave all its
                        # verts away has to be zeroed, and skipping the call would
                        # leave the old weight in place and DOUBLE the follow.
                        s.setShapeWeights(b, outp)
                        continue
                    for vi, w in bw.get(b, []):
                        ivi = int(vi)
                        nw = float(w)
                        if isbr and ivi in scale_br:
                            nw = w * scale_br[ivi]
                        elif (not isbr) and ivi in scale_nb:
                            nw = w * scale_nb[ivi]
                        if nw > _nc()._WRITE_MIN:
                            outp.append((ivi, nw))
                    if outp:
                        s.setShapeWeights(b, outp)
            except Exception as _we:
                for b, st in saved_stb.items():
                    try:
                        s.set_skin_to_bone_xform(b, st)
                    except Exception as _pe:
                        _note_pass_failure("set_skin_to_bone_xform", _pe)
                print(f"  WARN: bust-plate follow write failed mid-shape on "
                      f"{s.name!r} ({_we!r}) -- STBs restored", file=sys.stderr)
                continue
            for b, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(b, st)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            if chain_tgt:
                # Report the SPLIT, not just a count: "n verts transplanted" hides
                # the case where every vert chose the base bone and the pass was
                # a no-op wearing a success.
                per = {}
                for ci in chain_tgt.values():
                    per[ci] = per.get(ci, 0) + 1
                print(f"  bust-plate transplant: {s.name!r} "
                      + ", ".join(f"{per.get(i,0)} -> {_nc().BUST_PLATE_CHAIN[i]}"
                                  for i in sorted(per))
                      + (f" (added {want})" if want else " (no new bones)"),
                      file=sys.stderr)
            total += len(scale_br)
            dirty = True

        if dirty:
            _nc()._hide_virtual_body(nf)
            try:
                atomic_nif_save(nf, dst_path)
            except Exception as _se:
                _note_pass_failure(
                    "_sync_bust_plate_follow_postwrite/save", _se, dst_path)
                return 0
        return total
    except Exception as _le:
        print(f"  WARN: _sync_bust_plate_follow_postwrite died: {_le!r} -- "
              f"bust-plate follow skipped for this piece", file=sys.stderr)
        _note_pass_failure("_sync_bust_plate_follow_postwrite", _le)
        return 0
