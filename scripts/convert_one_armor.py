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

"""Convert ONE armor's mesh pair for fast diagnose/fix/verify loops on a single
piece -- WITHOUT diverging from what a real batch run produces.

    python scripts/convert_one_armor.py [--mo2-ini <ModOrganizer.ini>]
        [--slots 0xNNN] [--esp <plugin>]... [--no-ube-prefix] [--flat]
        <mod_dir> <mesh_subdir> <stem> [out_dir]

PARITY IS THE POINT (#single-vs-batch-parity). This harness builds the SAME
work-item tuple the batch builds and hands it to the SAME
`auto_convert._nif_convert_worker`, so there is one conversion code path, not
two. It was NOT always so, and the divergence was measured on 2026-07-29:
against the batch's output for the same piece and flags, the old harness moved
223/755 verts of one shape by up to 3.6u, 365/2495 of another by up to 1.9u,
and shifted bone weights by up to 0.25. Every number taken from the old
harness was therefore describing a mesh the user does not ship.

The three things that differed, all now matched:

  1. SLOTS. The old harness hand-rolled its own ARMA scan over the mod's own
     ESPs only, and a BodySlide-output mod (no ESP) yielded 0 -- which
     silently disables every slot-gated pass (anti-poke on slot 32/49,
     slot-aware inflation, reskin band, scale reach, boot far-thigh
     exclusion). That is the vert delta above. Now built with the batch's own
     `ube_patcher.build_nif_slot_map` + `auto_convert._make_slot_resolver`,
     over the mod's ESPs AND the game's plugins AND (last) the converter's own
     output patch ESP, with the same weight-agnostic `_0`/`_1` folding
     (#slot0-weight-partner). Unresolved slots are now a hard ERROR: a
     slots=0 run is not comparable to production, and it has produced two
     false findings already. Override deliberately with `--slots`.
  2. OUTPUT ROOT. The batch writes `<out>/meshes/!UBE/<subdir>/`; the old
     harness wrote `<out>/meshes/<subdir>/`. That changes the `Meshes\\...`
     relative path baked into the NIF's physics-XML and BODYTRI extra data.
     Now `!UBE` by default (`--no-ube-prefix` to opt out).
  3. ALT-TEXTURE SHAPES. The batch passes the ARMO/ARMA alt-texture target
     shape names so the morph-cap merge leaves colour-variant shapes alone;
     the old harness passed nothing, so those shapes could merge differently.
     Now collected with the batch's own `collect_alt_texture_shape_names`.

Still deliberately NOT reproduced (batch-level, not per-NIF): the incremental
up-to-date skip, first-writer-wins collision claiming, and everything after
the mesh (ESP merge, coverage, SkyPatcher INI, textures).

The `meshes` ancestor in the output path is REQUIRED: `_read_source_hdt_xml_text`
resolves the physics XML by walking up to a directory literally named `meshes`
and re-rooting there. With no such ancestor it returns None,
`_hdt_collider_shape_names` returns an EMPTY SET, and every collider/soft-body
protection quietly no-ops -- measured once as a harness reporting NO colliders
and grafting a bust to 0.770 that the pipeline correctly left at 0.000.
`--flat` exists only to reproduce that old broken layout on purpose.

Recipe flags come from the environment and are read AT IMPORT, so set them
when launching (they cannot be applied afterwards):

  CBBE2UBE_THIGH_STANDOFF=1.0 python scripts/convert_one_armor.py ...

Then: python scripts/armor_clip_diag.py <out>/<stem>_1.nif <mod>/.../<stem>_1.nif
"""
import argparse
import os
import sys
from pathlib import Path

# This script lives in <repo>/scripts/, so the repo root is its parent's parent.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".pynifly"))
sys.path.insert(0, str(REPO))

from src import paths, auto_convert as ac, ube_patcher   # noqa: E402


def _say(msg: str) -> None:
    """print() that cannot kill the run on a console codec.

    The converter's own result reasons contain non-cp1252 characters (e.g. the
    U+2194 in an HDT bone warning). In the real pipeline those reach a UTF-8
    log; on a Windows cp1252 console `print` raises UnicodeEncodeError -- which
    aborted this script AFTER converting `_0` and BEFORE `_1`, silently
    producing a half-converted pair. A diagnostic line must never be able to
    do that."""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"))


def _candidate_esps(mod_dir: Path, extra: "list[Path]") -> "list[Path]":
    """Plugins to resolve slots from, in the batch's own order of authority.

    The batch reads the SOURCE mod's ESPs, and reaches vanilla/other-mod ARMAs
    through its discovery sweep. A single-piece run has no sweep, so stand in
    for it with the game's plugins and -- last -- the converter's own output
    patch ESP, which carries an ARMA (with the real BOD2) for every piece it
    has already converted."""
    out: "list[Path]" = []
    out += [Path(p) for p in extra]
    try:
        out += ac._find_source_esps(Path(mod_dir))
    except Exception:
        pass
    try:
        # GAME_DATA is a pathsep-joined LIST (Stock Game / Game Root / real
        # Data can all be present); `export_to_env` writes it.
        for chunk in (os.environ.get(paths.GAME_DATA_ENV) or "").split(
                os.pathsep):
            if chunk and Path(chunk).is_dir():
                out += sorted(Path(chunk).glob("*.es[pml]"))
    except Exception:
        pass
    try:
        mods = paths.mods_root()
        out_mod = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
        if mods:
            out += sorted((Path(mods) / out_mod).glob("*.es[pml]"))
    except Exception:
        pass
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen and Path(p).is_file():
            seen.add(k)
            uniq.append(Path(p))
    return uniq


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--mo2-ini")
    ap.add_argument("--slots", default=None,
                    help="override the resolved biped mask, e.g. 0x134")
    ap.add_argument("--esp", action="append", default=[],
                    help="extra plugin to resolve slots from (repeatable)")
    ap.add_argument("--no-ube-prefix", action="store_true",
                    help="write to meshes/<subdir> instead of meshes/!UBE/<subdir>")
    ap.add_argument("--flat", action="store_true",
                    help="DIAGNOSTIC ONLY: write with no `meshes` ancestor, "
                         "which disables physics-XML resolution")
    ap.add_argument("mod_dir")
    ap.add_argument("mesh_subdir")
    ap.add_argument("stem")
    ap.add_argument("out_dir", nargs="?", default=None)
    args = ap.parse_args()

    if args.mo2_ini:
        os.environ["CBBE2UBE_MO2_INI"] = args.mo2_ini
    if not os.environ.get("CBBE2UBE_MO2_INI"):
        print(__doc__)
        print("ERROR: no MO2 instance configured. Pass `--mo2-ini "
              "<ModOrganizer.ini>` or set CBBE2UBE_MO2_INI.")
        sys.exit(2)

    mod_dir = Path(args.mod_dir)
    subdir = args.mesh_subdir.strip("/\\")
    stem = args.stem
    out = Path(args.out_dir) if args.out_dir else Path(os.environ["TEMP"],
                                                       "one_armor")
    paths.export_to_env(paths.discover_layout())

    # --- output root: mirror the batch (meshes/!UBE/<subdir>) ---
    if args.flat:
        dst_dir = out
    else:
        dst_dir = out / "meshes"
        if not args.no_ube_prefix:
            dst_dir = dst_dir / "!UBE"
        dst_dir = dst_dir / subdir.replace("/", os.sep)
    dst_dir.mkdir(parents=True, exist_ok=True)

    # --- slots: the batch's own resolver, same weight-agnostic folding ---
    rel_for_slots = f"{subdir}/{stem}_1.nif"
    if args.slots is not None:
        slots = int(args.slots, 0)
        how = "--slots override"
    else:
        esps = _candidate_esps(mod_dir, [Path(p) for p in args.esp])
        try:
            slot_map = ube_patcher.build_nif_slot_map(esps)
        except Exception as e:
            print(f"ERROR: could not build the slot map: {e!r}")
            sys.exit(2)
        slots = ac._make_slot_resolver(slot_map)(rel_for_slots)
        how = f"resolved from {len(esps)} plugin(s)"
    if not slots:
        print(f"ERROR: biped slots resolved to 0 for {rel_for_slots!r} "
              f"({how}).\n"
              f"       A slots=0 run is NOT comparable to a real conversion: "
              f"every slot-gated pass\n"
              f"       (anti-poke on slot 32/49, slot-aware inflation, reskin "
              f"band, scale reach,\n"
              f"       boot far-thigh exclusion) silently does not run. That "
              f"has produced two\n"
              f"       false findings already, so this is an error, not a "
              f"warning.\n"
              f"       Fix: pass --esp <the plugin whose ARMA names this mesh>, "
              f"or --slots 0x134\n"
              f"       (read the real mask off the armour's ARMA BOD2).")
        sys.exit(2)

    # --- alt-texture protected shapes: as the batch collects them ---
    try:
        alt_tex = ube_patcher.collect_alt_texture_shape_names(
            ac._find_source_esps(mod_dir)) or set()
    except Exception:
        alt_tex = set()

    ref = ac._find_ube_body_ref()
    ref = str(ref) if ref else None
    srcd = mod_dir / "meshes" / subdir.replace("/", os.sep)
    print(f"  slots  : 0x{slots:x} ({how})")
    print(f"  alt-tex: {len(alt_tex)} protected shape name(s)")
    print(f"  body ref: {ref}")
    print(f"  output : {dst_dir}")

    for w in ("_0", "_1"):
        src = srcd / f"{stem}{w}.nif"
        if not src.exists():
            print(f"  MISSING {src}")
            continue
        # THE work item the batch builds -- same tuple, same worker, so there
        # is exactly one conversion path. #single-vs-batch-parity
        item = (src, dst_dir / f"{stem}{w}.nif", ref, int(slots), alt_tex)
        r = ac._nif_convert_worker(item)
        _say(f"  {stem}{w}: {getattr(r, 'status', r)}"
             + (f"  -- {r.reason}" if getattr(r, "reason", "") else ""))


if __name__ == "__main__":
    main()
