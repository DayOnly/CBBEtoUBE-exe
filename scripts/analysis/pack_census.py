# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""ONE PASS over a converted pack: surface, BODYTRI, collision proxy, parity.

    python scripts/analysis/pack_census.py <mod-dir> [--headroom] [--limit N]

Reads each NIF ONCE and scores everything off that read, so it costs a
fraction of running the individual harnesses back to back. Every population
is printed with its exclusions -- 0/0 is not a pass.

READ `bodytri_of` BEFORE WRITING ANY EXTRA-DATA PROBE. pynifly's
`shape.extra_data()` stops at the first block it cannot build, and a
BodySlide body carries a `NiIntegersExtraData` LOCKEDNORM at index 0 -- so
it reports NO extra data on exactly the shapes that are bodies. That
produced two confidently wrong censuses before it was caught. Enumerate by
index over `extraDataCount`.

Reads each NIF once and scores everything off that read, so it costs a fraction
of running the individual harnesses back to back. Prints an explicit population
with every exclusion counted -- 0/0 is not a pass.
"""
import sys
import os
import glob
import re
import collections

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(_REPO / "scripts" / "analysis"))
import logging  # noqa: E402
logging.disable(logging.WARNING)
import numpy as np  # noqa: E402
from pyn.pynifly import NifFile  # noqa: E402
from src.tri import TriFile  # noqa: E402
from fold_census import score  # noqa: E402

BODY_NAMES = {"BaseShape", "3BA"}
PROXY = {"VirtualBody", "VirtualGround", "SkirtCol", "ButtCol"}


def is_textured(shape) -> bool:
    """Does this shape carry a REAL texture path?

    `bool(shape.textures)` is not that question, and answering it that way put
    40 of the census's 42 "untagged cloth shape" NIFs there. A source COLLISION
    shape ships textureless (`{}`), the converter re-adds it with the slot
    STRUCTURE but no paths (`{'Diffuse': '', 'Normal': ''}`), and a dict of
    empty strings is truthy -- so an invisible physics helper scored as cloth
    that should have been morphing. Measured on the pack: `Stabilizer` (180
    verts, the same shape in 20 outfits) reads `{}` at the source and
    `{'Diffuse': '', 'Normal': ''}` in our output.

    Note the converter's own carrier picker asks the truthy question too. It is
    not wrong today only because those shapes are dropped as "textureless
    collision shapes" during copy and re-added AFTER the BODYTRI step, so it
    never sees them -- a latent trap, not a fix."""
    try:
        return any(str(v).strip() for v in (shape.textures or {}).values())
    except Exception:
        return False


# Enumerated by INDEX, and shared so no census re-derives the version that
# silently reads empty. See `_census_common.bodytri_of`.
from _census_common import bodytri_of  # noqa: E402,F401


def _same_bytes(a, b) -> bool:
    """Are two files byte-identical? Size first, so the read is rare."""
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


_PARTNER_CACHE: dict = {}


def _partner_shape_names(nif_path) -> set:
    """Shape names of the `_0`/`_1` weight partner of `nif_path`, empty when
    there is none. Cached -- the same partner is asked about once from each
    half."""
    key = str(nif_path)
    if key in _PARTNER_CACHE:
        return _PARTNER_CACHE[key]
    out: set = set()
    base = key[:-4] if key.lower().endswith(".nif") else key
    for a, b in (("_0", "_1"), ("_1", "_0")):
        if base.endswith(a):
            q = base[: -len(a)] + b + ".nif"
            if os.path.exists(q):
                try:
                    out = set(sh.name for sh in NifFile(q).shapes)
                except Exception:
                    out = set()
            break
    _PARTNER_CACHE[key] = out
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = os.path.join(sys.argv[1], "meshes")
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if not os.path.isdir(root):
        print("no meshes dir under " + sys.argv[1])
        return 2

    nifs = sorted(glob.glob(os.path.join(root, "**", "*.nif"), recursive=True))
    nifs = [p for p in nifs
            if "_bsa_staging" not in p
            and "1stperson" not in os.path.basename(p).lower()]
    if limit:
        nifs = nifs[:limit]

    headroom = "--headroom" in sys.argv
    bust_min = []          # (min standoff, piece, shape) over the bust band
    stat = collections.Counter()
    fold_tot = inv_tot = judged = 0
    body_slot = body_tagged = untagged_cloth = 0
    proxy_pieces = ground_tagged = 0
    parity_bad = []
    oob = []
    # TRI/NIF SHAPE-NAME AGREEMENT. The out-of-bounds check above can only score
    # a tri shape the NIF also has (`counts.get(sh.name)` is None otherwise and
    # the shape is skipped) -- so a tri written for a DIFFERENT outfit entirely
    # scored CLEAN. The morph never matches at runtime either way, so this class
    # is DEAD sliders rather than dangerous ones, but it must not read as a pass.
    name_agree = 0
    name_partial = []
    name_alias = []
    name_disjoint = []
    tri_unreferenced = 0
    tri_missing = []
    # A shape NAMED `BaseShape` that is NOT the injected body. A mod may call one
    # of its own armour shapes that, and picked by NAME it is handed to every
    # consumer of `ube_body_shape` as the body -- which is how the UBE body's OSD
    # morphs came to be embedded verbatim on a 3618-vert cuirass part.
    # `_collect_tri_inputs` now splits by TOPOLOGY, but the pick ITSELF is still
    # name-only, so this class must stay VISIBLE rather than be assumed gone.
    baseshape_counts = collections.Counter()
    baseshape_where = collections.defaultdict(list)
    # Every `.tri` a NIF points at -- so the ones NOTHING points at can be found.
    # The reverse of `tri_missing`: correct morph data written, then orphaned by
    # a NIF that shipped without the BODYTRI string. Silent DEAD sliders.
    referenced_tris = set()
    # `_0`/`_1` written more than a day apart -- two builds in one garment.
    stale_pairs = []
    worst = []
    load_fail = []
    seen_pairs = set()

    for p in nifs:
        stat["nifs"] += 1
        try:
            nf = NifFile(p)
        except Exception as e:
            load_fail.append((os.path.relpath(p, root), repr(e)[:60]))
            continue
        shapes = list(nf.shapes)
        names = [s.name for s in shapes]

        for s in shapes:
            if s.name == "BaseShape":
                try:
                    n = len(s.verts)
                except Exception:
                    continue
                baseshape_counts[n] += 1
                baseshape_where[n].append(os.path.relpath(p, root))

        f_here = i_here = 0
        for s in shapes:
            if s.name in PROXY:
                continue
            try:
                v = np.asarray(s.verts, np.float64)
                f, i, u = score(v, s.tris, s.normals)
            except Exception:
                continue
            f_here += int(f.sum())
            i_here += int(i.sum())
            judged += int((~u).sum())
        fold_tot += f_here
        inv_tot += i_here
        if f_here:
            worst.append((f_here, i_here, os.path.relpath(p, root)))

        if any(n in BODY_NAMES for n in names):
            body_slot += 1
            tagged = set(s.name for s in shapes if bodytri_of(s))
            if tagged & BODY_NAMES:
                body_tagged += 1
            # XML-declared shapes are COLLIDERS (and simulated cloth the XML
            # already drives). A collider must NOT be tagged to morph -- the
            # pack has CTD history around altering collider shapes -- so
            # counting them as untagged cloth over-reports. Mods name theirs
            # anything (`ColLegs`, `Colision`, `collision body`), so the name
            # list alone cannot find them; read the piece's own XML.
            declared = set()
            _stem = p[:-6] if p[-6:-4] in ("_0", "_1") else p[:-4]
            _x = _stem + ".xml"
            if os.path.exists(_x):
                _t = open(_x, "rb").read().decode("utf-8", "ignore")
                declared = set(re.findall(
                    r'<per-(?:triangle|vertex)-shape\s+name="([^"]+)"', _t))
            cloth = [s.name for s in shapes
                     if is_textured(s)
                     and s.name not in BODY_NAMES and s.name not in PROXY
                     and s.name not in declared]
            if [c for c in cloth if c not in tagged]:
                untagged_cloth += 1

        if "SkirtCol" in names:
            proxy_pieces += 1
            stem = p[:-6] if p[-6:-4] in ("_0", "_1") else p[:-4]
            xmlp = stem + ".xml"
            if os.path.exists(xmlp):
                txt = open(xmlp, "rb").read().decode("utf-8", "ignore")
                m = re.search(
                    r'<per-triangle-shape name="SkirtCol">(.*?)</per-triangle-shape>',
                    txt, re.S)
                if m:
                    tg = re.search(r"<tag>([^<]*)</tag>", m.group(1))
                    if tg and tg.group(1).strip().lower() in ("ground", "body"):
                        ground_tagged += 1

        # BUST HEADROOM. #softcloth-seated-cap caps the extra lift softcloth
        # adds to cloth already outside the body, and the pass exists to give
        # the UBE bust jiggle room -- so the number that matters is the CLOSEST
        # approach in the breast band, not the median. A small minimum is where
        # the body will punch through under jiggle.
        if headroom and any(n in BODY_NAMES for n in names):
            try:
                from scipy.spatial import cKDTree
                bs = next(s for s in shapes if s.name in BODY_NAMES)
                tree = cKDTree(np.asarray(bs.verts, np.float64))
                for s2 in shapes:
                    if s2.name in BODY_NAMES or s2.name in PROXY:
                        continue
                    v2 = np.asarray(s2.verts, np.float64)
                    band = (v2[:, 2] >= 90) & (v2[:, 2] < 102) & (v2[:, 1] > 1.0)
                    if band.sum() < 30:
                        continue
                    d2, _ = tree.query(v2[band])
                    bust_min.append((float(d2.min()),
                                     os.path.relpath(p, root), s2.name))
            except Exception:
                pass

        base = p[:-6] if p[-6:-4] in ("_0", "_1") else None
        if base and base not in seen_pairs:
            p0, p1 = base + "_0.nif", base + "_1.nif"
            if os.path.exists(p0) and os.path.exists(p1):
                seen_pairs.add(base)
                stat["pairs"] += 1
                # #stale-weight-partner. `_complete_weight_partners` used to
                # fill a missing partner ONCE, EVER, so a `_0` copied by an old
                # build was never refreshed: the two halves of one piece came
                # from converter builds WEEKS apart and the engine blends
                # between them by body weight. It also poisons every pack-wide
                # census -- 4 of the 6 zero-weight bones in the 2026-09-06 pack
                # sat on those stale files and read as this build's defects.
                # A day of slack absorbs a long batch; anything more is a pair
                # no single run wrote.
                #
                # MTIME IS ONLY THE TRIGGER, NEVER THE VERDICT. The fix's
                # refresh deliberately SKIPS a partner that already matches
                # ("already a current copy; leave the mtime alone"), so a pair
                # can be weeks apart on disk and byte-identical in content --
                # current, and not a defect. Scoring the mtime alone reports a
                # permanent false positive that no run can ever clear, and it
                # gets re-investigated every time. So compare the BYTES and
                # count the identical ones separately, as an exclusion.
                try:
                    dt = abs(os.path.getmtime(p0) - os.path.getmtime(p1))
                    if dt > 86400:
                        if _same_bytes(p0, p1):
                            stat["pairs_mtime_skew_same_bytes"] += 1
                        else:
                            stale_pairs.append(
                                (os.path.relpath(p0, root), dt / 86400.0))
                except OSError:
                    pass
                try:
                    a = [(s.name, len(s.verts)) for s in NifFile(p0).shapes]
                    b = [(s.name, len(s.verts)) for s in NifFile(p1).shapes]
                    if len(a) != len(b):
                        parity_bad.append(
                            (os.path.relpath(p0, root),
                             "shape count %d vs %d" % (len(a), len(b))))
                    else:
                        bad = ["%s %d!=%d" % (a[i][0], a[i][1], b[i][1])
                               for i in range(len(a)) if a[i][1] != b[i][1]]
                        if bad:
                            parity_bad.append(
                                (os.path.relpath(p0, root), "; ".join(bad[:3])))
                except Exception:
                    pass

        # SCORE THE TRI THE NIF POINTS AT, not the one its stem derives.
        # Those are different files whenever a low-poly variant shares a stem
        # with the worn pair -- and that gap is exactly where the fix for
        # #tri-variant-collision lands: the variant that cannot carry the shared
        # TRI's offsets now ships with NO BODYTRI, so NioOverride is never asked
        # to move a vertex it lacks. Scored off the stem, that file still reads
        # as out of bounds and the fix reads as INERT. It is the same trap that
        # let the FIRST fix ship without anyone noticing it did nothing.
        #
        # A NIF with no BODYTRI cannot morph at runtime and so cannot index past
        # anything -- counted as an exclusion below, never as a pass.
        _refs = sorted({bt.strip().replace("\\", "/").lstrip("/")
                        for s in shapes for bt in bodytri_of(s) if bt.strip()})
        referenced_tris.update(r.lower() for r in _refs)
        trip = next((c for c in (os.path.join(root, r) for r in _refs)
                     if os.path.exists(c)), None)
        if not _refs:
            tri_unreferenced += 1
        elif trip is None:
            tri_missing.append("%s -> %s"
                               % (os.path.relpath(p, root), ", ".join(_refs)))
        if trip is not None:
            try:
                counts = dict((s.name, len(s.verts)) for s in shapes)
                tri = TriFile.load(trip)
                tri_names = set(sh.name for sh in tri.shapes)
                matched = tri_names & set(counts)
                rel_p = os.path.relpath(p, root)
                if not tri_names:
                    pass                       # an empty tri is its own problem
                elif not matched:
                    name_disjoint.append(
                        "%s: nif %s | tri %s"
                        % (rel_p, ",".join(sorted(counts)[:4]),
                           ",".join(sorted(tri_names)[:4])))
                elif matched != tri_names:
                    # #pair-tri-names makes the shared tri carry BOTH halves'
                    # shape names on purpose, so each half finds its own. The
                    # partner's names are then "tri-only" for this half BY
                    # DESIGN. Ask the pack rather than re-deriving a suffix
                    # rule: load the weight partner and see whose names those
                    # are. Anything left over is a real mismatch.
                    extra = tri_names - matched
                    if extra and extra <= _partner_shape_names(p):
                        name_alias.append(
                            "%s: partner-only %s"
                            % (rel_p, ",".join(sorted(extra)[:4])))
                    else:
                        name_partial.append(
                            "%s: tri-only %s"
                            % (rel_p, ",".join(sorted(extra)[:4])))
                else:
                    name_agree += 1
                for sh in tri.shapes:
                    n = counts.get(sh.name)
                    if n is None:
                        continue
                    mx = -1
                    for m in sh.morphs:
                        for (idx, _x, _y, _z) in m.offsets:
                            if idx > mx:
                                mx = idx
                    if mx >= n:
                        oob.append("%s: %s maxidx %d >= %d"
                                   % (os.path.relpath(p, root), sh.name, mx, n))
            except Exception:
                pass

    # ORPHANED `.tri`: written, correct, and pointed at by NOTHING.
    #
    # The scored population deliberately drops `1stperson*`, but those NIFs
    # still REFERENCE tris -- so their refs have to be collected here or every
    # first-person tri reads as an orphan. This is the only place the census
    # opens an excluded NIF, and it reads nothing but the BODYTRI strings.
    excluded = sorted(set(glob.glob(os.path.join(root, "**", "*.nif"),
                                    recursive=True))
                      - set(nifs))
    excluded = [q for q in excluded if "_bsa_staging" not in q]
    for q in excluded:
        try:
            for s in NifFile(q).shapes:
                for bt in bodytri_of(s):
                    if bt.strip():
                        referenced_tris.add(
                            bt.strip().replace("\\", "/").lstrip("/").lower())
        except Exception:
            pass
    tris_on_disk = [t for t in glob.glob(os.path.join(root, "**", "*.tri"),
                                         recursive=True)
                    if "_bsa_staging" not in t]
    orphan_tris = sorted(
        t for t in tris_on_disk
        if os.path.relpath(t, root).replace("\\", "/").lower()
        not in referenced_tris)
    orphan_bytes = sum(os.path.getsize(t) for t in orphan_tris)

    # AUTHORED `BaseShape`: the modal vert count is the injected body; anything
    # else carrying that name is an armour shape that merely shares it.
    body_verts = (baseshape_counts.most_common(1)[0][0]
                  if baseshape_counts else None)
    authored_base = sorted(
        (n, f) for n, fs in baseshape_where.items() if n != body_verts
        for f in fs)

    bar = "=" * 78
    print(bar)
    print("PACK CENSUS  " + sys.argv[1])
    print(bar)
    print("  NIFs read (no 1stperson, no _bsa_staging) : %d" % stat["nifs"])
    print("  failed to load                            : %d" % len(load_fail))
    for r, e in load_fail[:5]:
        print("      %s: %s" % (r, e))
    print("  _0/_1 pairs                               : %d" % stat["pairs"])
    print("")
    print("SURFACE")
    print("  vertices judged                           : %d" % judged)
    print("  FOLDED                                    : %d" % fold_tot)
    print("  INVERTED                                  : %d" % inv_tot)
    print("")
    print("BODYTRI  (the slider fix)")
    print("  body-slot NIFs                            : %d" % body_slot)
    print("  BODYTRI present on the body               : %d/%d%s"
          % (body_tagged, body_slot,
             "   OK" if body_tagged == body_slot else "   <== MUST equal"))
    print("  NIFs with an untagged cloth shape         : %d%s"
          % (untagged_cloth, "   <== should be 0" if untagged_cloth else "   OK"))
    print("")
    print("GENERATED COLLISION PROXY  (the physics fix)")
    print("  pieces still shipping a SkirtCol          : %d" % proxy_pieces)
    print("  of those, tagged ground/body              : %d%s"
          % (ground_tagged, "   <== should be 0" if ground_tagged else "   OK"))
    print("")
    print("WEIGHT-PAIR PARITY")
    print("  pairs with a vert-count mismatch          : %d%s"
          % (len(parity_bad), "   <== should be 0" if parity_bad else "   OK"))
    for r, m in parity_bad[:10]:
        print("      %s: %s" % (r, m))
    print("  pairs >1 DAY apart AND differing           : %d%s"
          % (len(stale_pairs),
             "   <== two builds in one garment" if stale_pairs else "   OK"))
    for r, d in sorted(stale_pairs, key=lambda x: -x[1])[:10]:
        print("      %s: %.1f days" % (r, d))
    print("    excluded: >1 day apart but BYTE-IDENTICAL: %d   (current copies"
          " the refresh deliberately left alone)"
          % stat["pairs_mtime_skew_same_bytes"])
    print("  TRI offsets out of bounds                 : %d%s"
          % (len(oob), "   <== should be 0" if oob else "   OK"))
    for line in oob[:10]:
        print("      " + line)
    print("  NIFs with no BODYTRI (cannot morph, not at risk) : %d"
          % tri_unreferenced)
    print("  BODYTRI pointing at a MISSING tri         : %d%s"
          % (len(tri_missing), "   <== should be 0" if tri_missing else "   OK"))
    for line in tri_missing[:8]:
        print("      " + line)
    # An orphan carrying NO SHAPES is a 6-byte header stub: inert, and the pack
    # ships five. Only an orphan WITH shapes is a piece whose sliders were
    # generated and then lost. Judged by parsing, not by byte count -- kept
    # apart so the row does not cry wolf every run and stop being read.
    fat_orphans = []
    for t in orphan_tris:
        try:
            if TriFile.load(t).shapes:
                fat_orphans.append(t)
        except Exception:
            fat_orphans.append(t)      # unreadable -> not dismissable
    print("  tri files on disk NOTHING points at       : %d (%d empty stub%s)%s"
          % (len(orphan_tris), len(orphan_tris) - len(fat_orphans),
             "" if len(orphan_tris) - len(fat_orphans) == 1 else "s",
             "   <== dead sliders, %.1f MB" % (orphan_bytes / 1048576.0)
             if fat_orphans else "   OK"))
    for t in (fat_orphans or orphan_tris)[:8]:
        print("      %s  (%d bytes)"
              % (os.path.relpath(t, root), os.path.getsize(t)))
    print("")
    print("BODY IDENTITY  (`ube_body_shape` picks the body by NAME, so a shape")
    print("a mod happens to call BaseShape is handed to every consumer as the")
    print("body. Halve the count for `_0`/`_1`.)")
    print("  injected-body vert count (modal)          : %s"
          % (body_verts if body_verts is not None else "n/a"))
    print("  BaseShape shapes at another vert count    : %d%s"
          % (len(authored_base),
             "   <== authored, NOT the body" if authored_base else "   OK"))
    for n, f in authored_base[:8]:
        print("      %s  (%d verts)" % (f, n))
    print("")
    print("TRI / NIF SHAPE-NAME AGREEMENT  (a tri whose names the NIF lacks is")
    print("scored CLEAN by the out-of-bounds check above -- it has nothing to")
    print("compare. The morph never matches at runtime: DEAD sliders.)")
    _named = (name_agree + len(name_alias) + len(name_partial)
              + len(name_disjoint))
    print("  NIFs scored (their own BODYTRI resolved)  : %d" % _named)
    print("  every tri shape named in the NIF          : %d" % name_agree)
    print("  tri also carries the PARTNER's names      : %d   OK, by design"
          % len(name_alias))
    print("      ^ #pair-tri-names aliases both halves into the shared tri so")
    print("        each half names its own shapes. The other half's names are")
    print("        then tri-only HERE, which is the fix working, not a defect.")
    for line in name_alias[:4]:
        print("      " + line)
    print("  SOME tri shapes the NIF does not have     : %d%s"
          % (len(name_partial),
             "   <== dead sliders" if name_partial else "   OK"))
    for line in name_partial[:8]:
        print("      " + line)
    print("  NO tri shape the NIF has                  : %d%s"
          % (len(name_disjoint),
             "   <== whole tri is dead" if name_disjoint else "   OK"))
    for line in name_disjoint[:8]:
        print("      " + line)
    if _named == 0:
        print("      (no BODYTRI resolved on any NIF -- 0/0 IS NOT A PASS)")
    if headroom:
        print("BUST HEADROOM  (closest approach in the breast band, z 90-102 front)")
        print("  cloth shapes measured                     : %d" % len(bust_min))
        if bust_min:
            bust_min.sort()
            vals = np.array([b[0] for b in bust_min])
            print("  min standoff  p05 / median / max          : "
                  "%.3f / %.3f / %.3f u" % (np.percentile(vals, 5),
                                            np.median(vals), vals.max()))
            tight = [b for b in bust_min if b[0] < 0.05]
            print("  shapes closer than 0.05u (poke risk)      : %d%s"
                  % (len(tight), "   <== inspect" if tight else "   OK"))
            for d3, r, n in bust_min[:8]:
                print("      %.3fu  %s :: %s" % (d3, r, n))
        else:
            print("  (none measured -- 0/0 IS NOT A PASS)")
        print("")
    print("")
    worst.sort(reverse=True)
    print("WORST 12 PIECES (folded, inverted)")
    for f, i, r in worst[:12]:
        print("    %6d %5d  %s" % (f, i, r))
    if stat["nifs"] == 0:
        print("")
        print("  !! 0 NIFs read -- 0/0 IS NOT A PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
