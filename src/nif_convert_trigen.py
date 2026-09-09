"""BODYTRI / morph-TRI generation and the body-morph caches: OSD loading, the per-body morph stack / amplitude / differential caches, carrier choice, morph flags, the post-reimport TRI refresh and the nude-morph preflight.

Split out of nif_convert.py on 2026-09-01 (split step 4). Imported by name into
nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in
nif_convert are reached through `_nc()` at CALL time, so flags read at
import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still
apply."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import sys

from .atomic_io import (
    atomic_nif_save, atomic_copy, atomic_write_bytes, atomic_tri_save)
from .nif_convert_bodyrefs import (  # noqa: E402
    _CBBE_3BA_VERTS, _CBBE_BODY_NAME_HINTS, _UBE_BODY_NAME_HINTS,
    _BODYSLIDE_OUT_HINTS, _FEMBODY_REL, _BODY_DISCOVERY_CACHE,
    _MOD_DIR_LIST_CACHE, _GLOB_FIRST_MEMO,
    _iter_femalebody_nifs, _shape_has_3ba_topology, _find_cbbe_base_body,
    _find_ube_femalebody, _sorted_mod_dirs, _glob_first_in_mods,
    _find_ube_shapedata, _find_ube_template_body, _find_ube_body_osd,
    _find_user_preset_body,
)
from .nif_convert_telemetry import (  # noqa: E402
    _PASS_FAILURES, _PASS_FAILURES_THIS_PIECE, _PASS_EFFECTS,
    _PASS_EFFECTS_THIS_PIECE, _note_pass_failure, _note_pass_effect,
    _piece_pass_effects, pass_effect_summary, _begin_piece_pass_log,
    _piece_pass_failures, pass_failure_summary,
)



def armor_relpath_under_meshes(src_path) -> "Path | None":
    """A NIF's path RELATIVE to its `meshes` root, or None if it has no such
    root -- the key every armour-TRI lookup is done under.

    Written out twice, line for line (audit F102): once on the copy path and
    once on the body-swap path, one of them under the comment "same logic as
    phase 2". That comment is the tell -- a copy that ANNOUNCES it is a copy is
    one edit away from being wrong, and the two entry functions have already
    drifted in five other places this way.

    Both cases are tried because mod authors ship either capitalisation, and
    the FIRST marker wins: a path containing `meshes` twice (a mod folder
    literally named "meshes") must resolve against the outermost, which is what
    `parts.index` returns.
    """
    try:
        parts = Path(src_path).parts
        for marker in ("meshes", "Meshes"):
            if marker in parts:
                i = parts.index(marker)
                return Path(*parts[i + 1:])
    except Exception:
        pass
    return None

def _nc():
    """The monolith, resolved at call time (never at import: circular)."""
    return sys.modules[__package__ + ".nif_convert"]


_OSD_CACHE: "dict[Path, object]" = {}  # path -> OsdFile

_BODY_MORPH_AMP_CACHE: "dict[Path, np.ndarray]" = {}  # osd_path -> per-body-vert outward size-morph amplitude

_MORPH_STACK_MIN = 0.5          # peak delta below this cannot drive a bust poke

def _cached_osd_load(path: Path):
    """Cache OsdFile.load to avoid re-parsing the 11 MB body OSD per NIF."""
    from .osd import OsdFile
    p = Path(path)
    cached = _OSD_CACHE.get(p)
    if cached is None:
        cached = OsdFile.load(p)
        _OSD_CACHE[p] = cached
    return cached

# OSD morph names matched by substring to build the per-vert outward-amplitude map.
_MORPH_SIZE_KEYWORDS = (
    "breast", "butt", "belly", "cleav", "nipple", "hip", "thigh", "waist",
    "big", "pregn", "chub", "wide", "tummy", "gut", "ass", "pelvis",
    # "glute" covers UBE/3BA GluteSize/Spread/Height/... sliders (missed by "butt").
    # "trochanter" = hip-bone slider. Both are genuine outward-volume zones.
    "glute", "trochanter",
)

_BODY_MORPH_DIFF_CACHE: dict = {}

def _body_array_digest(arr) -> str:
    """Content digest of a body vert/normal array, for cache keys.

    THE POISONING CLASS THIS EXISTS FOR (proven 2026-08-18, sibling probe):
    the morph-map caches keyed on the OSD path (or path+count) while their
    VALUES were computed from the caller's weight-specific body arrays. The
    `_0` and `_1` bodies share a vert count, so the first weight to call
    pinned the clip-risk map for every later conversion in the process --
    which is why serial batch runs were self-consistent while pool runs
    flipped ~22 SMP pieces per scheduling. A body-dependent value must carry
    the body's identity in its key; ~2ms of sha1 against a 0.5-0.9s compute.
    """
    a = np.ascontiguousarray(arr)
    import hashlib as _hl
    return _hl.sha1(a.tobytes()).hexdigest()[:16]

def _cached_body_morph_differential(osd_path: Path,
                                    body_verts: "np.ndarray",
                                    body_normals: "np.ndarray",
                                    ) -> "np.ndarray | None":
    """Per-body-vert clearance the worst slider TAKES AWAY from a hugging garment.

    For body vert i with outward normal n_i, over the k-neighbourhood the
    clearance passes already use:

        need[i] = max over sliders m, neighbours j of  (d_m[j] - d_m[i]) . n_i

    Zero when a slider merely translates or inflates uniformly (the deltas
    cancel), positive only where the body RESHAPES relative to the surface a
    garment sits on. Same formula `conform_to_source_standoff` applies to the
    bust and the back, evaluated on the body alone so every pass can share it.

    Uses `_cached_body_morph_stack`, which selects sliders by MEASURED peak
    delta. `_cached_body_morph_amplitude` selects by NAME
    (_MORPH_SIZE_KEYWORDS): 105 of the OSD's 202 sliders against the stack's 145,
    so the two morph-aware mechanisms have never read the same sliders.

    Cached on (osd path, vert count, both array digests). The sibling cache
    `_BODY_MORPH_STACK_CACHE` USED TO key on the path ALONE while its value
    depended on `n_verts`, so a first call with a different count poisoned every
    later one -- closed 2026-08-18 with the rest of the body-blind-key class; it
    now keys on `(path, count)`. Re-verified 2026-09-06. This note stated the
    defect in the PRESENT tense long after it was fixed, which sends a reader
    hunting a bug that is not there.
    """
    if osd_path is None or body_verts is None or body_normals is None:
        return None
    v = np.asarray(body_verts, dtype=np.float64)
    n = np.asarray(body_normals, dtype=np.float64)
    if v.shape != n.shape or len(v) == 0:
        return None
    # (path, count) alone CANNOT tell the `_0` body from the `_1` body -- same
    # topology, different geometry -- and the value depends on both arrays.
    # See _body_array_digest for the measured poisoning this fixes.
    key = (Path(osd_path), len(v),
           _body_array_digest(v), _body_array_digest(n))
    hit = _BODY_MORPH_DIFF_CACHE.get(key)
    if hit is not None:
        return hit
    stack = _cached_body_morph_stack(osd_path, len(v))
    if stack is None or not len(stack):
        _BODY_MORPH_DIFF_CACHE[key] = None
        return None
    from scipy.spatial import cKDTree       # imported per-function in this file
    kk = min(_nc().BUST_NEIGHBORHOOD_K, len(v))
    dd, jj = cKDTree(v).query(v, k=kk)
    if kk == 1:
        dd = dd[:, None]
        jj = jj[:, None]
    keep = dd <= _nc().BUST_NEIGHBORHOOD_RADIUS
    out = np.zeros(len(v))
    for dm in stack:
        d = np.asarray(dm, dtype=np.float64)
        du = np.einsum("ij,ij->i", d, n)
        dj = np.einsum("nkj,nj->nk", d[jj], n)
        np.maximum(out, np.where(keep, dj - du[:, None], -np.inf).max(axis=1),
                   out=out)
    np.clip(out, 0.0, None, out=out)
    _BODY_MORPH_DIFF_CACHE[key] = out
    return out

def _cached_body_morph_amplitude(osd_path: Path,
                                 body_normals: "np.ndarray",
                                 n_verts: int) -> "np.ndarray | None":
    """Per-body-vert OUTWARD morph amplitude = max over the major size/shape
    sliders of `max(0, delta . outward_normal)`. This is "how far this body
    vertex can grow outward at runtime" — the clip-risk map that drives adaptive
    armor clearance. Cached per (OSD path, count, NORMALS digest) — the value
    depends on the caller's weight-specific normals, and a path-only key let
    the first weight pin the map for the whole process (the measured pool
    nondeterminism; see _body_array_digest). Returns None if no OSD."""
    if osd_path is None or body_normals is None:
        return None
    p = Path(osd_path)
    key = (p, int(n_verts), _body_array_digest(body_normals))
    cached = _BODY_MORPH_AMP_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        osd = _cached_osd_load(p)
    except Exception:
        return None
    bn = np.asarray(body_normals, dtype=np.float64)
    amp = np.zeros(n_verts, dtype=np.float64)
    for m in osd.morphs:
        nm = m.name.lower()
        if not any(k in nm for k in _MORPH_SIZE_KEYWORDS):
            continue
        for idx, dx, dy, dz in m.offsets:
            if idx >= n_verts:
                continue
            outward = dx * bn[idx, 0] + dy * bn[idx, 1] + dz * bn[idx, 2]
            if outward > amp[idx]:
                amp[idx] = outward
    _BODY_MORPH_AMP_CACHE[key] = amp
    return amp

_BODY_MORPH_STACK_CACHE: "dict[Path, np.ndarray | None]" = {}

def _cached_body_morph_stack(osd_path: Path, n_verts: int) -> "np.ndarray | None":
    """(M, n_verts, 3) float32 stack of the body's SHAPE-DRIVING slider deltas.

    `_cached_body_morph_amplitude` collapses the sliders to a per-vert MAXIMUM,
    which is enough to know "this vert can grow", but not to compare two body
    verts under the SAME slider -- and that comparison is the whole of
    #bust-morph-residual. Kept as float32 and filtered to sliders that actually
    move something (peak delta >= _MORPH_STACK_MIN), so the real OSD's 202
    morphs reduce to the few dozen that can drive a bust poke.
    Cached per OSD path; returns None if no OSD.
    """
    if osd_path is None:
        return None
    p = Path(osd_path)
    # The value's SHAPE depends on n_verts -- the differential's docstring has
    # warned about this key since it was written; closed 2026-08-18 with the
    # rest of the body-blind-key class (_body_array_digest). The stack itself
    # reads no body arrays, so path+count fully identifies it.
    skey = (p, int(n_verts))
    if skey in _BODY_MORPH_STACK_CACHE:
        return _BODY_MORPH_STACK_CACHE[skey]
    try:
        osd = _cached_osd_load(p)
    except Exception:
        _BODY_MORPH_STACK_CACHE[skey] = None
        return None
    keep: "list[np.ndarray]" = []
    for m in osd.morphs:
        if not m.offsets:
            continue
        arr = np.asarray(m.offsets, dtype=np.float64)
        idx = arr[:, 0].astype(np.int64)
        d = arr[:, 1:4]
        ok = (idx >= 0) & (idx < n_verts)
        if not ok.any():
            continue
        if np.linalg.norm(d[ok], axis=1).max() < _MORPH_STACK_MIN:
            continue
        buf = np.zeros((n_verts, 3), dtype=np.float32)
        buf[idx[ok]] = d[ok]
        keep.append(buf)
    out = np.stack(keep) if keep else None
    _BODY_MORPH_STACK_CACHE[skey] = out
    return out

# Weight variants of one stem, in the order they may claim the shared `.tri`.
# `_0` FIRST, and that is measured, not reasoned -- see `_tri_is_owning_variant`.
_TRI_VARIANT_PRIORITY = ("_0", "_1", "")


def _variant_suffix(stem: str) -> str:
    """`"_0"`, `"_1"` or `""` -- which weight variant this stem is."""
    return stem[-2:] if stem.endswith(("_0", "_1")) else ""


def _tri_owner_path(src_path, variant_sources):
    """Absolute SOURCE path of the variant that OWNS this stem's `.tri`, or
    None when the variant set was not resolved for this conversion.

    `variant_sources` maps a weight suffix (`"_0"` / `"_1"` / `""`) to the
    resolved source path of that variant. It is built where sources are
    resolved -- through the mod list / VFS / BSA index, exactly as the file
    being converted was resolved. Passing it is what makes this correct ACROSS
    MODS: the competing variant routinely ships in a DIFFERENT mod than the one
    being converted, and a BSA-resolved source is staged ALONE, so probing for
    a sibling next to `src_path` sees nothing and the guard passes everything.
    #tri-variant-collision

    None means "not resolved" (a single-file convert, or a test), and the
    caller falls back to the on-disk sibling probe.
    """
    if not variant_sources:
        return None
    for suf in _TRI_VARIANT_PRIORITY:
        hit = variant_sources.get(suf)
        if hit:
            return Path(hit)
    return None


def _tri_is_owning_variant(src_path, variant_sources=None) -> bool:
    """Is this the weight variant that OWNS the shared `.tri`?

    **`_0` owns it.** That is measured, not reasoned. This first shipped picking
    `_1`, on the argument that the body the deltas are measured against is the
    `_1` reference -- and it put a nipple through a leather cuirass in game
    within hours. The armour that HAD been working carried a `_0`-derived TRI,
    and the two are not close on a bust piece: on that cuirass's chest shape 92
    of 155 morphs differ, worst `Juicy_breasts` at 2.16u, `BreastsTBD` 1.96u.
    Matching the working file against both candidates settled it -- `_0`-derived
    mean |delta| 0.00002u, `_1`-derived 0.00157u with a 2.16u worst.

    Why it matters so much more here than on the boots this was first checked on
    (0.17u worst): `_0` and `_1` differ most exactly where a bust piece has its
    geometry, so deltas computed from them diverge most there too. The game
    applies ONE tri across the whole weight range, so this choice is a real fit
    decision at the chest, not bookkeeping.

    Decided from the SOURCE side, never the destination. The source set is
    complete before conversion starts, whereas a destination sibling may not be
    written yet and, in a worker pool, is being written by a DIFFERENT PROCESS
    -- so any shared in-memory hint would not reach it. That is the same
    cross-process assumption that made this a race in the first place.

    A piece with no other resolved variant owns its own TRI whatever its suffix,
    so a `_1`-only armour never silently loses body morphs.

    A NO-SUFFIX variant does NOT own it when a `_0` or `_1` sibling exists.
    #tri-variant-collision. `foo.nif`, `foo_0.nif` and `foo_1.nif` all derive
    the same `foo.tri`, and the no-suffix stem does not end in `_1`, so it and
    `_0` BOTH returned True here -- two writers, last one wins. Where the
    no-suffix file is a lower-poly variant (a first-person or world model) the
    survivor's morphs address vertices it does not have. Measured on the shipped
    pack, 5 such entries across 3 stems:

        GauntletsF   1544 verts   TRI indexes to  3013
        Outfit       2467 verts   TRI indexes to 13705
        ClothF1st     495 verts   TRI indexes to  1544

    The owning variant keeps the TRI; see `_tri_fits_variant` for whether the
    others may still POINT at it.

    `variant_sources`: the resolved variant set for this stem (see
    `_tri_owner_path`). WITHOUT it this falls back to probing for a sibling file
    NEXT TO `src_path`, which is only correct when every variant ships in one
    mod -- and they routinely do not. The probe also cannot see the
    no-suffix-plus-`_1`-only case at all (neither ends in a claimed suffix, so
    both answered True); resolving the set closes that hole too.
    """
    try:
        p = Path(src_path)
        stem = p.stem
    except Exception:
        return True                      # unparseable -> generate, never skip
    owner = _tri_owner_path(src_path, variant_sources)
    if owner is not None:
        try:
            return _variant_suffix(owner.stem) == _variant_suffix(stem)
        except Exception:
            return True
    if stem.endswith("_1"):
        try:
            return not p.with_name(stem[:-2] + "_0" + p.suffix).is_file()
        except Exception:
            return True                  # cannot tell -> generate rather than skip
    if not stem.endswith("_0"):
        # no-suffix variant: the weight pair owns the shared TRI if it exists
        try:
            return not p.with_name(stem + "_0" + p.suffix).is_file()
        except Exception:
            return True
    return True                          # `_0` owns it


def _tri_fits_variant(src_path, variant_sources=None) -> bool:
    """May this NIF POINT at the shared `.tri` its stem derives?

    Only if the variant that OWNS that TRI has the same per-shape vertex
    counts. A first-person or low-poly file sharing a stem with the worn pair
    does not, and a BODYTRI pointing at the pair's TRI then asks NioOverride to
    move vertices the shape does not have. See #tri-variant-collision.

    Read from the SOURCE side, which is complete before conversion starts -- the
    destination sibling may still be unwritten, and in a worker pool is being
    written by a different process.

    `variant_sources` resolves the owner ACROSS MODS (see `_tri_owner_path`).
    Without it this probes for a sibling next to `src_path`; for a BSA-resolved
    source that is a staging directory holding that source ALONE, which is the
    shape the first version of this guard had and why it was inert on the real
    pack -- the same five out-of-bounds entries shipped after it.

    `_0` and `_1` are the same mesh at two weights and share the TRI by design,
    so a pair member never withholds its BODYTRI. That short-circuit is also
    what keeps the guard cheap: no NIF is read on the common path.

    **THAT ARGUMENT IS TRUE OF THE GEOMETRY AND FALSE OF THE SHAPE NAMES**, and
    the gap it leaves is measured: on the 2026-09-06 pack, 54 of 1536 pairs are
    named differently in their two halves (`BodyF_0`/`BodyF_1`,
    `BootsF:0`/`BootsF:1`, `Dress`/`Dress1`), and the half the tri was not built
    from names NOTHING in it -- 42 losing every morph. This guard waves all of
    them through, because it compares vertex counts and never names.

    Deliberately still does. Withholding the BODYTRI would not rescue those
    pieces; they would morph nothing either way, and the 12 partial cases would
    additionally lose the body morphs they still get. The fix belongs at the
    PRODUCER: `#pair-tri-names` puts both halves' names in the one tri. See
    `pair_shape_aliases`.
    """
    try:
        p = Path(src_path)
        stem = p.stem
    except Exception:
        return True
    owner = _tri_owner_path(src_path, variant_sources)
    if owner is None:
        if stem.endswith("_0") or stem.endswith("_1"):
            return True
        try:
            owner = p.with_name(stem + "_0" + p.suffix)
            if not owner.is_file():
                return True              # nothing else claims the TRI
        except Exception:
            return True
    try:
        if owner.resolve() == p.resolve():
            return True                  # this file IS the owner
    except Exception:
        if str(owner) == str(p):
            return True
    if (_variant_suffix(stem) in ("_0", "_1")
            and _variant_suffix(owner.stem) in ("_0", "_1")):
        return True                      # the weight pair, by design
    try:
        pyn = _nc()._pynifly()
        mine = {s.name: len(s.verts) for s in pyn.NifFile(filepath=str(p)).shapes}
        theirs = {s.name: len(s.verts)
                  for s in pyn.NifFile(filepath=str(owner)).shapes}
    except Exception:
        return True                      # cannot tell -> behave as before
    shared = set(mine) & set(theirs)
    if not shared:
        return True
    return all(mine[n] == theirs[n] for n in shared)

def _reset_morph_flags(shape) -> None:
    """Set shape.flags to match the hand-built UBE convention:

      * alpha=False shapes (most cloth)  -> 0xE   (bits 1, 2, 3)
      * alpha=True  shapes (translucent) -> 0x8000E (bits 1, 2, 3, 19)

    The 0x8000 bit is the alpha-sorter — required only when the shape
    has a NiAlphaProperty. Setting it uniformly (which we used to do
    via task #65) appears to interact badly with NioOverride for
    non-carrier shapes: hand-authored UBE cloth uses 0xE on every
    non-alpha cloth piece, and a hand-authored UBE armor is the only UBE armor in our
    test set that's been confirmed to follow body sliders.

    No-op on pynifly errors.
    """
    try:
        has_alpha = bool(getattr(shape, "has_alpha_property", False))
        target = (_nc().BODYTRI_SHAPE_FLAGS_ALPHA if has_alpha
                  else _nc().BODYTRI_SHAPE_FLAGS_OPAQUE)
        if int(getattr(shape, "flags", 0)) != target:
            shape.flags = target
    except Exception:
        pass

def _collect_tri_inputs(nif, body_vert_count=None):
    """Split a written NIF's shapes into the three inputs `generate_armor_tri`
    takes: armour verts IN BODY SPACE, the injected body shapes, and per-vert
    extremity fractions.

    Extracted from THREE verbatim copies (both convert paths and the post-
    reimport TRI refresh). They had already drifted in their comments, and when
    triangles needed threading through to the generator every copy had to be
    found and edited separately.

    The body-space offset matters: `generate_armor_tri` matches each armour vert
    to the body by nearest-neighbour, so a shape with a non-identity transform
    would otherwise sample the wrong body region. The extremity fraction damps
    finger/toe verts to ~0 morph while a sleeve or boot spanning forearm and
    hand keeps full morph over its forearm half.

    `body_vert_count`: the vertex count of the body the TRI's verbatim body
    morphs are indexed against. THE INJECTED BODY IS IDENTIFIED BY TOPOLOGY, NOT
    BY NAME. A mod is free to call one of its own armour shapes `BaseShape`, and
    4 NIFs in the pack do -- two of them a 3618-vert cuirass part. Classified by
    name alone that shape was pulled out of the armour set and handed to
    `generate_armor_tri` as a body shape, which then embedded the UBE body's OSD
    morphs VERBATIM: offsets indexing to 29297 on a 3618-vertex mesh, two of the
    five out-of-bounds entries the pack census reports. `generate_armor_tri`
    bounds-filters those offsets against the body REFERENCE's vert count (its
    `#osd-bounds` guard), and its comment states the assumption that broke here
    -- "body_verts shares the injected BaseShape's topology".

    So a shape carrying the injected-body name at a DIFFERENT vertex count is an
    authored shape that merely shares the name: it goes in the armour set and
    gets its own K-NN-propagated morphs, at its own vertex count.

    `VirtualBody` is never armour whatever it measures -- it is the SMP collision
    proxy we generate ourselves, its topology is per-piece by construction, and
    it must not morph. It is also inert on the other side: `generate_armor_tri`
    only ever emits a verbatim body TriShape for `BaseShape`.

    With no `body_vert_count` (an unmeasured caller) this falls back to the
    name-only split, i.e. the behaviour before the topology check.
    """
    armor_shape_verts: "dict[str, np.ndarray]" = {}
    armor_vert_ef: "dict[str, np.ndarray]" = {}
    body_in_dst: "set[str]" = set()
    for s in nif.shapes:
        if s.name in _nc().UBE_BODY_INJECT_NAMES:
            if not (s.name == "BaseShape" and body_vert_count
                    and len(s.verts) != int(body_vert_count)):
                body_in_dst.add(s.name)
                continue
        armor_shape_verts[s.name] = (
            np.asarray(s.verts, dtype=np.float64) + _nc().shape_body_offset(s))
        ef = _nc()._extremity_vert_fraction(s, len(s.verts))
        if ef is not None and ef.size:
            armor_vert_ef[s.name] = ef
    return armor_shape_verts, body_in_dst, armor_vert_ef

def _normalize_shader_for_morph(shape) -> None:
    """DISABLED (no-op). The env-map flag (Shader_Flags_1 bit 7) does NOT
    block NioOverride morphing. The former flag-clear also caused
    save_shader_attributes() to truncate the BSShaderTextureSet and drop
    the EnvMask (slot 5). Left as a no-op so call sites are unchanged.
    """
    return

def _pick_bodytri_carriers(nif, *, exclude_body: bool = False,
                           all_cloth: bool = False) -> "list[object]":
    """Pick exactly ONE shape per NIF to receive a BODYTRI extra-data
    block, matching the hand-authored BodySlide UBE convention.

    `exclude_body=True` skips the body-shape preference (BaseShape/3BA) so the
    result is a CLOTH carrier even on a body-swap NIF. Used by the HDT-XML
    generator: the body is the kinematic COLLIDER (emitted as a per-triangle
    shape), never a simulated per-vertex cloth — picking BaseShape there made
    the injected body flop as soft-body cloth while the real cape got no
    physics. The default (False) keeps the body-first preference the BODYTRI
    MORPH carrier wants.

    Carrier preference (in order):
      1. BaseShape / 3BA — body shape, if present. NioOverride morphs
         all shapes in the TRI when BODYTRI is on the body shape; on
         a cloth shape NioOverride often skips other shapes.
      2. Cloth shape — for body-less NIFs (slot-49 cloth-only armors).
      3. Hand/foot fallback — for gauntlet/boot-only NIFs.

    Single-carrier: NioOverride opens the TRI once via the BODYTRI
    reference and applies per-name morphs to every shape in the TRI.

    Returns a list with 0 or 1 entry -- or, with `all_cloth=True`, every cloth
    candidate in ranked order (the hand-authored majority convention).
    """
    # Preference 1: BODY SHAPE as carrier.
    #
    # The "88/93 sampled slot-32 UBE NIFs" figure this used to cite does not
    # describe carrier choice, and a re-census that tried to correct it was
    # itself wrong: pynifly's `extra_data()` walks by index and STOPS at the
    # first block it cannot build, and a BodySlide body carries a
    # NiIntegersExtraData `LOCKEDNORM` at index 0 -- so every body shape read
    # as carrying NO extra data at all, BODYTRI included. Enumerating by
    # `get_extra_data(target_index=i)` for i in range(extraDataCount) instead:
    #
    #     nude body BaseShape        LOCKEDNORM + BODYTRI
    #     hand-built armor BaseShape LOCKEDNORM + BODYTRI x2
    #     that armor's cloth shapes  BODYTRI, on EVERY one
    #
    # So the authored arrangement is BODYTRI on the body AND on every cloth
    # shape -- not one or the other. `all_cloth=True` reproduces it.
    # VirtualBody is excluded -- it's a Hidden physics proxy, not the visible
    # body shape NioOverride wants to morph.
    BODY_CARRIER_NAMES = ("BaseShape", "3BA")
    body_shapes = [s for s in nif.shapes if s.name in BODY_CARRIER_NAMES]
    if not exclude_body and body_shapes and not all_cloth:
        return [body_shapes[0]]

    def _with_body(picked):
        """`all_cloth` means "the BODY (unless excluded) AND every cloth shape",
        so the body belongs on EVERY return path -- not just the one that found
        cloth candidates.

        MEASURED: 2 body-swap NIFs in the pack shipped with no BODYTRI on their
        `BaseShape` at all, which is what the census's "BODYTRI present on the
        body: 589/591" counts. Traced on one of them: at BODYTRI time the NIF
        held exactly two shapes, `BaseShape` and a cloth shape whose name
        contains "chain" -- a NON_CLOTH_SHAPE_KEYWORD. So `candidates` was empty
        (the cloth keyword-excluded, the body in BODYTRI_CARRIER_EXCLUDE), and
        the carrier-of-last-resort branch below returned that one cloth shape and
        silently dropped the body. Preference 1 in this docstring says the body
        is the carrier NioOverride morphs every TRI shape from, so losing it is
        the opposite of the intent."""
        if not all_cloth or exclude_body:
            return picked
        head = list(body_shapes[:1])
        return head + [s for s in picked if s not in head]

    candidates: list = []
    hand_fallbacks: list = []
    # `all_cloth` keeps a SEPARATE, relaxed list. The keyword and
    # extremity drops below exist to stop a rigid prop being chosen as THE
    # single carrier; when every shape is tagged that rationale does not
    # apply, and applying it anyway leaves a garment's own metal trim
    # untagged while the garment morphs -- the trim then detaches from the
    # cloth it sits on. Hand-authored armour tags its metal shapes too.
    relaxed: list = []
    for s in nif.shapes:
        if not (s.textures or {}):
            continue
        if s.name in _nc().BODYTRI_CARRIER_EXCLUDE:
            continue
        relaxed.append(s)
        nlow = s.name.lower()
        if any(kw in nlow for kw in _nc().NON_CLOTH_SHAPE_KEYWORDS):
            continue
        # Use strict weight-fraction test: arm-shell pieces (Bracers, Guards,
        # <1% extremity weight) are good carriers; only actual hand/foot skin
        # (Hands_2 97%, Gloves_1 71%) goes to the last-resort fallback.
        if _nc()._shape_is_extremity_dominant(s):
            hand_fallbacks.append(s)
            continue
        candidates.append(s)
    if not candidates and not hand_fallbacks:
        # Every textured shape was excluded by rigid-name keywords (e.g. a
        # chest garment named "straps" hits the "shoulder" keyword). A NIF
        # with no carrier never loads its TRI -> nothing morphs. Pick the
        # largest textured shape as the carrier (a truly rigid prop has near-
        # zero deltas so the morph is harmless).
        rigid_named = [
            s for s in nif.shapes
            if (s.textures or {}) and s.name not in _nc().BODYTRI_CARRIER_EXCLUDE
        ]
        non_ext = [s for s in rigid_named
                   if not _nc()._shape_is_extremity_dominant(s)]
        pool = non_ext or rigid_named
        if not pool:
            return _with_body([])
        return _with_body([max(pool, key=lambda s: len(s.verts))])

    CLOTH_KEYWORDS = (
        "corset", "leather", "fabric", "tabard", "panties", "panty",
        "skirt", "tassel", "tasset", "belt", "shirt", "robe", "dress",
        "cloak", "cape", "scarf", "loin",
    )
    def rank_key(s):
        nlow = s.name.lower()
        kw_match = next(
            (i for i, kw in enumerate(CLOTH_KEYWORDS) if kw in nlow),
            len(CLOTH_KEYWORDS),
        )
        return (kw_match, -len(s.verts))
    if candidates:
        candidates.sort(key=rank_key)
        if all_cloth:
            # Authored arrangement: the BODY (unless excluded) AND every cloth
            # shape. The body goes FIRST so shapes[0] -- which orders the TRI --
            # is still the body, matching the single-carrier path.
            head = [] if exclude_body else list(body_shapes[:1])
            ranked = sorted(relaxed, key=rank_key)
            return head + [c for c in ranked if c not in head]
        return [candidates[0]]
    # Carrier-of-last-resort: NIF contains only hand/foot shapes. The per-armor
    # TRI for these NIFs only contains hand/foot morph entries — no body deltas
    # leak onto fingers — so picking the hand/foot shape is safe.
    hand_fallbacks.sort(key=rank_key)
    return _with_body([hand_fallbacks[0]])

# #morphtri-name-cache. The morph-TRI gate is consulted by SEVEN passes, and
# each consultation re-parsed the whole source TRI. Profiled on a five-layer
# piece: 12 calls, 13.98s of a 181.8s conversion, cut to 0.95s (-93%).
#
# Keyed by path AND mtime+size, not path alone: the converter WRITES TRIs (the
# re-import refresh re-emits one mid-run), and a path-only cache would serve a
# stale shape list to a later pass. Per-process by construction, which is what
# the worker pool needs -- there is no shared state to invalidate.
#
# END-TO-END SPEEDUP IS NOT ESTABLISHED. The function-level saving is measured
# and isolated; total wall time moved only +1.2% on a single before/after pair,
# and an unrelated function (`_cast`) moved 8s in that same pair -- a TRI cache
# cannot slow ray casting, so the pair carries too much noise to support a
# wall-clock claim. Output is byte-identical (verts 0.000000).
_MORPH_TRI_NAME_CACHE: "dict[tuple, set]" = {}


def pair_shape_aliases(mine, theirs) -> "dict[str, str]":
    """{my shape name -> the PARTNER's name for the same shape}. #pair-tri-names

    THE PROBLEM THIS SOLVES, measured on the 2026-09-06 pack: authors routinely
    give the `_0` and `_1` halves of one garment DIFFERENT shape names --
    `BodyF_0`/`BodyF_1`, `BootsF:0`/`BootsF:1`, `Robe_0`/`Robe_1`,
    `Dress`/`Dress1`. We build ONE tri per garment from the `_0` half (which is
    correct and measured -- see `_tri_is_owning_variant`) and point BOTH halves
    at it, so the `_1` half names NOTHING in the tri it points at and its
    sliders move nothing. **54 of 1536 pairs: 42 lose every morph, 12 lose
    some, and `_0` never loses anything.** `_1` is the half that ships, since
    actors sit near weight 100.

    `_tri_fits_variant` is the guard that should have caught it and does not:
    it checks per-shape vertex counts for every OTHER variant and
    short-circuits the weight pair on the argument that the two halves are "the
    same mesh at two weights". True of the GEOMETRY, false of the NAMES.

    `mine` / `theirs` are ORDERED `[(shape name, vert count), ...]` for the two
    halves. Alignment is BY INDEX -- the halves are one mesh saved twice and
    authored shape order is preserved, so index i is the same shape.

    FAILS CLOSED, and each refusal is a case where an alias would be wrong:

      * different shape COUNTS -> `{}`. The halves are not the same mesh (this
        is the one pair of the 54 that cannot take this fix: 5 shapes vs 4).
      * any index whose vert counts DISAGREE -> `{}` for the whole pair. One
        delta table can only serve both names if the topology matches, and a
        partial map would emit morphs against the wrong vertex indices.
      * a partner name already used by one of MY shapes -> that entry only is
        dropped, so an alias can never shadow a real shape's own morphs.
    """
    mine = list(mine or ())
    theirs = list(theirs or ())
    if not mine or len(mine) != len(theirs):
        return {}
    if any(int(a[1]) != int(b[1]) for a, b in zip(mine, theirs)):
        return {}
    own = {str(n) for n, _ in mine}
    out: "dict[str, str]" = {}
    for (a, _), (b, _) in zip(mine, theirs):
        a, b = str(a), str(b)
        if a != b and b not in own:
            out[a] = b
    return out


def pair_alias_map(src_path, variant_sources) -> "dict[str, str]":
    """`pair_shape_aliases` for THIS half, read off the two SOURCE NIFs.

    Returns `{}` -- never raises, never blocks a conversion -- when the flag is
    off, when this file is not a weight variant, when no partner was resolved,
    or when the two halves fail the topology precondition.

    **SOURCE, and paired BY PATH.** `variant_sources` maps a weight suffix to
    the source path resolved through the same mod list / VFS / BSA chain that
    found this file, so it sees ACROSS MODS -- the two halves of one garment
    routinely ship in different mods, and probing for a sibling next to
    `src_path` is what made the first `#tri-variant-collision` guard inert.

    It cannot read the partner's DESTINATION instead: `_0` owns the tri and is
    converted first, so when the owner writes it the `_1` destination does not
    exist yet. The source names are the right answer anyway, because authored
    shape names are preserved through conversion; the shapes we ADD (the
    injected body, the collision proxy) carry the same name in both halves and
    so never produce an alias.
    """
    try:
        if not _nc().PAIR_TRI_NAMES or not variant_sources:
            return {}
        p = Path(src_path)
        mine_suf = _variant_suffix(p.stem)
        if mine_suf not in ("_0", "_1"):
            return {}
        other = variant_sources.get("_1" if mine_suf == "_0" else "_0")
        if not other:
            return {}
        other = Path(other)
        try:
            if other.resolve() == p.resolve():
                return {}
        except Exception:
            if str(other) == str(p):
                return {}
        pyn = _nc()._pynifly()
        mine = [(s.name, len(s.verts))
                for s in pyn.NifFile(filepath=str(p)).shapes]
        theirs = [(s.name, len(s.verts))
                  for s in pyn.NifFile(filepath=str(other)).shapes]
        return pair_shape_aliases(mine, theirs)
    except Exception as _e:
        _note_pass_failure("pair_alias_map", _e)
        return {}


def _source_morph_tri_shape_names(src_path: "Path") -> "set[str]":
    """Shape names covered by the SOURCE mod's own BodySlide morph TRI (it sits
    next to the source NIF as `<armor-stem>.tri`). These shapes are morphed by
    that TRI at runtime, so they don't need the M6 reskin's scale-bone morph and
    are better served by their stable source skin. Empty set if the source ships
    no such TRI (-> keep the reskin). Best-effort; never raises.

    Memoised -- see _MORPH_TRI_NAME_CACHE for the measurement."""
    try:
        stem = src_path.stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        tri = src_path.parent / (stem + ".tri")
        if not tri.is_file():
            return set()
        try:
            _st = tri.stat()
            _key = (str(tri).lower(), _st.st_mtime_ns, _st.st_size)
        except Exception:
            _key = None            # cannot key it safely -> parse, don't cache
        if _key is not None:
            _hit = _MORPH_TRI_NAME_CACHE.get(_key)
            if _hit is not None:
                return set(_hit)   # copy: callers treat the result as theirs
        from .tri import TriFile
        names = {sh.name for sh in TriFile.load(tri).shapes}
        if _key is not None:
            _MORPH_TRI_NAME_CACHE[_key] = set(names)
        return names
    except Exception:
        return set()

def _refresh_armor_tri_after_reimport(
        dst_path: "Path", tri_path, *,
        fallback_body_verts=None) -> bool:
    """Re-emit the BODYTRI once the LATE passes have re-imported shapes that
    weren't in the NIF when the TRI was first written.  #tri-reimport-refresh

    THE BUG THIS FIXES (in-game proven: physics cloth collapses slowly and
    survives `smp reset`). Pass 1 drops TEXTURELESS shapes -- collision proxies
    and physics-framework shapes -- with `if not (s.textures or {}): continue`.
    The NIF is saved, and the auto-TRI is generated from THAT saved NIF, so
    those shapes get no morph table. Only afterwards does
    `_finalize_hdt_physics` re-import them ("Collision-proxy preservation",
    flagged Hidden). Final NIF has them; the TRI never does.

    At runtime BodyMorph then moves the CLOTH to the player's preset (measured
    up to ~8u) while its COLLIDER and chain STABILISER stay at base shape
    (their source tables morph up to ~11u). The cloth is dragged off the
    geometry meant to support it, finds nothing under it, and sags -- every
    load, which is why an FSMP reset never helped and why the mesh measures
    perfectly clean on disk.

    NOT fixable by copying the source TRI's tables across, even though the
    re-import is verbatim so vertex indices line up 1:1: the source tables are
    named for the SOURCE body's sliders (3BA) and ours for UBE's -- measured
    overlap 3 of 143. They would be dead weight the UBE actor never drives. So
    regenerate against the UBE OSD, which is what every other shape already got.

    No-op unless the NIF really does carry a shape the TRI lacks, so the common
    piece pays one shape-name comparison and no K-NN. A shape whose propagated
    deltas all fall under `min_delta` legitimately stays out of the rebuilt TRI.
    Returns True if the TRI was rewritten."""
    if tri_path is None:
        return False
    tri_path = Path(tri_path)
    if not tri_path.is_file():
        return False
    try:
        from .tri import TriFile
        from .sliderset_gen import generate_armor_tri

        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
        have = {sh.name for sh in TriFile.load(tri_path).shapes}
        if not have:
            return False                    # not our generated TRI / unreadable
        missing = [s.name for s in nf.shapes if s.name not in have]
        if not missing:
            return False                    # nothing re-imported -> nothing to do

        osd_path = _find_ube_body_osd()
        if osd_path is None:
            return False
        body_osd = _cached_osd_load(osd_path)

        body_shape = _nc().ube_body_shape(nf)
        if body_shape is not None:
            body_verts_arr = np.asarray(body_shape.verts, dtype=np.float64)
        elif fallback_body_verts is not None:
            body_verts_arr = np.asarray(fallback_body_verts, dtype=np.float64)
        else:
            return False
        if not len(body_verts_arr):
            return False

        (armor_shape_verts, body_in_dst,
         armor_vert_ef) = _collect_tri_inputs(nf, len(body_verts_arr))
        if not armor_shape_verts:
            return False

        carriers = _pick_bodytri_carriers(nf)
        tri = generate_armor_tri(
            armor_shape_verts,
            body_verts_arr,
            body_osd,
            body_shape_name="BaseShape",
            include_body_shapes=body_in_dst,
            carrier_shape_name=carriers[0].name if carriers else None,
            armor_vert_extremity_fractions=armor_vert_ef,
        )
        got = {sh.name for sh in tri.shapes}
        if not (got - have):
            return False                    # nothing gained -> leave the TRI alone
        atomic_tri_save(tri, tri_path)
        print(f"  #tri-reimport-refresh: {tri_path.name} += "
              f"{sorted(got - have)}", file=sys.stderr)
        return True
    except Exception as _e:
        _note_pass_failure("_refresh_armor_tri_after_reimport", _e)
        return False

def check_ube_nude_morph_files() -> "list[str]":
    """Pre-flight check for the UBE NUDE body/hands/feet morph (.tri) files.

    The UBE nude race skin morphs to the player's RaceMenu sliders via a
    BodySlide-built `.tri` that sits NEXT TO the mesh (femalebody_tangent.tri,
    femalehands_tangent.tri, femalefeet_tangent.tri) and is found by name
    convention -- NOT via a `BODYTRI` string extra-data (that's the armor
    convention; the nude meshes carry none). BodySlide writes the .tri only
    when 'Build Morphs' is checked. If the body has its .tri but hands/feet
    don't, the hands/feet stay at base shape while the body morphs to the
    preset -> they look mismatched / 'not UBE'. This is purely a BodySlide
    build issue (the converter doesn't touch the nude skin), but it's a
    common, hard-to-spot trap, so we surface it. Returns warning strings.
    """
    warns: "list[str]" = []
    parts = [
        ("Body", "meshes/!UBE/Body/femalebody_tangent_1.nif"),
        ("Hands", "meshes/!UBE/Hands/femalehands_tangent_1.nif"),
        ("Feet", "meshes/!UBE/Feet/femalefeet_tangent_1.nif"),
    ]
    found = []
    for label, pat in parts:
        p = _glob_first_in_mods(pat)
        if p is None:
            warns.append(
                f"UBE {label}: no !UBE/{label} nude mesh found -- the UBE "
                f"{label} BodySlide group isn't built.")
            continue
        p = Path(p)
        # femalebody_tangent_1.nif -> femalebody_tangent.tri (weight stripped)
        stem = p.stem
        if stem.endswith(("_0", "_1")):
            stem = stem[:-2]
        tri = p.with_name(stem + ".tri")
        found.append((label, tri.is_file()))
        if not tri.is_file():
            warns.append(
                f"UBE {label}: mesh present but NO morph file ({tri.name}) -- "
                f"the nude {label} won't follow body sliders (stays at base "
                f"shape while the body morphs). Rebuild the UBE {label} in "
                f"BodySlide with 'Build Morphs' checked.")
    # Highlight the specific asymmetry that produces the 'body is UBE but
    # hands/feet aren't' symptom.
    fd = dict(found)
    if fd.get("Body") and (fd.get("Hands") is False or fd.get("Feet") is False):
        warns.append(
            "UBE nude skin: the BODY has morph data but the HANDS/FEET do "
            "not -- this is exactly the 'body morphs UBE, hands/feet stay "
            "CBBE-shaped' mismatch. Rebuild Hands + Feet with Build Morphs.")
    return warns
