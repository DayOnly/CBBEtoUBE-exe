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

"""POST-RECONVERT GATE: XML-registered shapes carrying bones WE ADDED that the
piece's own physics XML never declares. The AUTHOR DIFFERENTIAL, which is what the guard scores and what the
defect actually is.

COUNTING EVERY UNDECLARED BONE IS THE WRONG MEASURE and reads 250 where the
defect is 10: `_audit_registered_shape_declared_bones`
already records that 336 registered shapes in the pack carry undeclared bones
their own AUTHOR shipped, so "undeclared" is not by itself wrong. Only bones WE
added can free-fall a piece that used to work.

So this pairs every registering pack NIF with its SOURCE (the VFS winner, the
same rule the converter uses) and reports `ours - declared - (author - declared)`,
exactly as the guard computes it -- then splits by shape class and, for the ones
we can act on, by whether a declared ancestor exists ON THAT SHAPE.

    python scripts/analysis/registered_bone_audit.py [<pack meshes/!UBE>] [<MO2 ini>] [--out PATH]

Exit 0 clean / 1 a violation we caused / 2 nothing measured.

WRITES NOTHING unless `--out PATH` is given; then the per-shape rows go to
PATH as JSON, and only there. The default pack lives INSIDE the modlist
instance, and a measuring tool must not change what it measures: until
2026-09-21 this wrote `registered_bone_audit.json` beside the pack on every
run -- into the instance, and before the verdict printed. The tables and the
verdict on stdout are the report; the JSON is an opt-in extract.

MEASURED 2026-08-23 on the pre-fix pack: 10 violating shapes over 174 checked
pieces, every one a shape WE create (a `<name>Col` bust-split clone), and ZERO
authored shapes carrying bones we added. After #collider-declared-bones the
expected result is 0. Those numbers were counted over WEIGHT 1 ONLY (this
enumerated `*_1.nif` until 2026-09-21) and are not comparable to a run now.

WEIGHTS: both weights, first-person INCLUDED, via
`standoff_audit.output_nifs(PACK, exclude_first_person=False)`. This is a
per-SHAPE physics census, and `x_0.nif` carries its own registered shapes and
its own bones: a bone that lands on the weight-0 half free-falls that piece
exactly as one on weight 1 would. Weight 0 is also where a post-guard repair
can act -- `_postflight_sync_weight_partner_jiggle` grafts a partner's jiggle
bone onto whichever weight lacks it, after the in-converter guard has run -- so
a `_1`-only census was blind to a plausible producer, not just to half a count.
First-person is kept ON PURPOSE: `output_nifs` drops it by default for a
BUST-COVERAGE reason, which has nothing to do with a physics bone. A
first-person mesh that registers physics can free-fall like any other.

Do NOT widen this to "any undeclared bone on a registered shape": that counts the
AUTHOR's own arrangement and reads 250 where the defect is 10. Only bones we
added can free-fall a piece that previously worked.

SKELETON DISCIPLINE: paths exported before the first nif_convert call, bone count
asserted -- an unloaded skeleton makes every bone read as unparented.
"""
import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

# `_REPO` is the CANONICAL spelling and it is not cosmetic:
# `tests/test_analysis_repo_root.py` scans for exactly this form, and a script
# declaring its root any other way is INVISIBLE to that check -- which is how
# six scripts once ran off a root pointing at `scripts/`.
_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(_REPO))
_ap = argparse.ArgumentParser(
    description="Collider/softbody bones WE added that the piece's own physics "
                "XML never declares. Exit 0 clean / 1 violation / 2 nothing "
                "measured.")
_ap.add_argument("pack", nargs="?", type=Path,
                 help="pack meshes/!UBE (default: the CBBEtoUBE Auto mod's)")
_ap.add_argument("mo2_ini", nargs="?", help="ModOrganizer.ini to resolve sources")
_ap.add_argument("--out", type=Path, metavar="PATH",
                 help="write the violating rows as JSON to PATH (default: "
                      "write nothing)")
ARGS = _ap.parse_args()
if ARGS.mo2_ini:
    os.environ["CBBE2UBE_MO2_INI"] = ARGS.mo2_ini

from src import paths                                   # noqa: E402
_lay = paths.discover_layout()
paths.export_to_env(_lay)
from pyn import pynifly                                 # noqa: E402
from src import nif_convert as nc                       # noqa: E402
from scripts.analysis import standoff_audit as sa       # noqa: E402

_parents = nc._actor_skeleton_bone_parents()
print(f"actor skeleton: {len(_parents)} parent link(s)")
if len(_parents) < 100:
    raise SystemExit("SKELETON UNLOADED -- refusing to report.")

PACK = ARGS.pack or (
    Path(paths.mods_root()) / "CBBEtoUBE Auto" / "meshes" / "!UBE")
_en = paths.enabled_mods(_lay)
_mods = [d for d in sorted(_lay.mods_root.iterdir())
         if d.is_dir() and (_en is None or d.name in _en)
         and "CBBEtoUBE" not in d.name and "UBE Converter" not in d.name]


def source_for(rel: Path):
    """VFS winner providing meshes/<rel> -- LAST enabled mod wins."""
    sub = rel.parent.as_posix()
    hit = None
    for d in _mods:
        if (d / "meshes" / sub.replace("/", os.sep) / rel.name).is_file():
            hit = d
    return (hit / "meshes" / sub.replace("/", os.sep) / rel.name) if hit else None


tot = collections.Counter()
by_shape = collections.Counter()
by_bone = collections.Counter()
rows = []

# BOTH weights, first-person KEPT -- see WEIGHTS: in the docstring.
nifs = sa.output_nifs(PACK, exclude_first_person=False)
print(f"{len(nifs)} pack NIF(s), both weights, first-person included",
      flush=True)
for i, p in enumerate(nifs):
    if i % 300 == 0:
        print(f"  ...{i}", flush=True)
    try:
        dn = pynifly.NifFile(filepath=str(p))
        reg = (set(nc._hdt_collider_shape_names(p, nif=dn))
               | set(nc._hdt_softbody_shape_names(p, nif=dn)))
    except Exception:
        tot["unreadable (EXCLUDED)"] += 1
        continue
    if not reg:
        continue
    txt = nc._read_source_hdt_xml_text(p, nif=dn)
    declared = set(re.findall(r'<bone\s+name="([^"]+)"', txt or ""))
    if not declared:
        tot["registers but NO declarations readable (UNCHECKED)"] += 1
        continue
    rel = p.relative_to(PACK)
    src = source_for(rel)
    if src is None:
        tot["source NOT resolvable (EXCLUDED, cannot diff vs author)"] += 1
        continue
    tot["pieces checked"] += 1
    try:
        sn = pynifly.NifFile(filepath=str(src))
    except Exception:
        tot["source unreadable (EXCLUDED)"] += 1
        continue
    author = {s.name: set((getattr(s, "bone_weights", None) or {}).keys())
              for s in sn.shapes}
    hit = False
    for s in dn.shapes:
        if s.name not in reg:
            continue
        ours = set((getattr(s, "bone_weights", None) or {}).keys())
        added = sorted((ours - declared) - (author.get(s.name, set()) - declared))
        if not added:
            continue
        hit = True
        in_src = s.name in author
        kind = "AUTHORED shape, bones WE added" if in_src else "SHAPE WE CREATED"
        tot[kind] += 1
        tot[f"bones: {kind}"] += len(added)
        by_shape[f"{kind[:18]}  {s.name}"] += 1
        fixable = sum(1 for b in added
                      if nc._nearest_declared_ancestor(b, declared, ours))
        tot[f"...relabellable ({kind[:18]})"] += fixable
        for b in added:
            by_bone[b] += 1
        rows.append({"nif": str(rel), "shape": s.name, "kind": kind,
                     "added": added, "relabellable": fixable,
                     "in_source": in_src})
    if hit:
        tot["pieces WITH a real (author-differential) violation"] += 1

print("\n=== POPULATION ===")
for k, v in tot.most_common():
    print(f"  {v:6}  {k}")
print("\n=== SHAPES ===")
for k, v in by_shape.most_common(25):
    print(f"  {v:5}  {k}")
print("\n=== BONES WE ADDED ===")
for k, v in by_bone.most_common(15):
    print(f"  {v:5}  {k}")
# ONLY where the caller says. Never beside the pack (that is inside the modlist
# instance) and never in the repo (hygiene fails on artefacts, and they go stale).
if ARGS.out:
    ARGS.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\nwrote {ARGS.out} ({len(rows)} violating shape instance(s))")
else:
    print(f"\n{len(rows)} violating shape instance(s); no file written "
          f"(--out PATH writes them as JSON)")

# EXIT CODE so a reconvert can be GATED on this, not merely informed by it.
#   0 clean   1 a violation WE caused   2 nothing measured
# The third is the one that matters: an empty result and a clean result are
# indistinguishable downstream, and reading one as the other is the failure this
# whole family of audits exists to prevent ([[feedback_census_population]]).
if not tot["pieces checked"]:
    print("NO PIECE WAS CHECKED -- this audit measured NOTHING.",
          file=sys.stderr)
    raise SystemExit(2)
_ours = tot["SHAPE WE CREATED"] + tot["AUTHORED shape, bones WE added"]
print(f"\nVERDICT: {_ours} shape(s) carry a bone WE added that the piece's own "
      f"physics XML never declares, over {tot['pieces checked']} piece(s) "
      f"checked.")
if _ours:
    print("  Each one can free-fall its piece in game -- see "
          "#collider-declared-bones.", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
