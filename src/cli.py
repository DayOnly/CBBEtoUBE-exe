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

"""Command-line interface."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import refit


# Auto-discovered at runtime (no hardcoded modpack paths). None until
# resolved from the installed CBBE/UBE body mods.
DEFAULT_CBBE_DIR = None
DEFAULT_UBE_DIR = None


def _resolve_body_dirs(args) -> None:
    """Fill --cbbe-dir / --ube-dir from auto-discovery when unset. The dirs
    are the parent folders of the discovered femalebody NIFs."""
    from . import paths as _paths
    from . import nif_convert as _nc
    _paths.export_to_env(_paths.discover_layout())
    if getattr(args, "cbbe_dir", None) is None:
        b = _nc._find_cbbe_base_body("_1")
        args.cbbe_dir = b.parent if b is not None else None
    if getattr(args, "ube_dir", None) is None:
        b = _nc._find_ube_femalebody("_1")
        args.ube_dir = b.parent if b is not None else None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cbbe-to-ube", description="Refit CBBE armor NIFs to UBE.")
    p.add_argument("--cbbe-dir", type=Path, default=DEFAULT_CBBE_DIR,
                   help="Folder containing femalebody_0.nif and femalebody_1.nif for CBBE.")
    p.add_argument("--ube-dir", type=Path, default=DEFAULT_UBE_DIR,
                   help="Folder containing femalebody_0.nif and femalebody_1.nif for UBE.")
    p.add_argument("-v", "--verbose", action="count", default=0)

    sub = p.add_subparsers(dest="cmd", required=True)

    refit_p = sub.add_parser("refit", help="Refit a single NIF.")
    refit_p.add_argument("input", type=Path)
    refit_p.add_argument("--out", type=Path, required=True,
                         help="Output NIF path (or directory if --out ends with a slash).")

    pair_p = sub.add_parser("refit-pair", help="Refit a _0/_1 weight pair together.")
    pair_p.add_argument("weight0", type=Path)
    pair_p.add_argument("weight1", type=Path)
    pair_p.add_argument("--out-dir", type=Path, required=True)

    batch_p = sub.add_parser("refit-batch", help="Refit every NIF in a folder recursively.")
    batch_p.add_argument("input_dir", type=Path)
    batch_p.add_argument("--out-dir", type=Path, required=True)
    return p


def _load_refs(cbbe_dir: Path, ube_dir: Path) -> tuple[refit.References, refit.References]:
    return (
        refit.References.load(cbbe_dir / "femalebody_0.nif", ube_dir / "femalebody_0.nif"),
        refit.References.load(cbbe_dir / "femalebody_1.nif", ube_dir / "femalebody_1.nif"),
    )


def _pick_refs_for(path: Path, refs_w0, refs_w1):
    if path.stem.endswith("_0"):
        return refs_w0
    if path.stem.endswith("_1"):
        return refs_w1
    # Unpaired NIF: just use weight-1 (the more common default).
    return refs_w1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _resolve_body_dirs(args)  # fill body dirs from auto-discovery when unset
    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    refs_w0, refs_w1 = _load_refs(args.cbbe_dir, args.ube_dir)

    if args.cmd == "refit":
        out = args.out
        if str(out).endswith(("/", "\\")) or out.is_dir():
            out = out / args.input.name
        refit.refit_nif(args.input, out, _pick_refs_for(args.input, refs_w0, refs_w1))
        logging.info("wrote %s", out)
        return 0

    if args.cmd == "refit-pair":
        args.out_dir.mkdir(parents=True, exist_ok=True)
        refit.refit_pair(args.weight0, args.weight1, args.out_dir, refs_w0, refs_w1)
        logging.info("wrote pair to %s", args.out_dir)
        return 0

    if args.cmd == "refit-batch":
        for w0, w1 in refit.iter_armor_pairs(args.input_dir):
            rel = (w0 or w1).relative_to(args.input_dir).parent
            dest = args.out_dir / rel
            dest.mkdir(parents=True, exist_ok=True)
            if w0:
                refit.refit_nif(w0, dest / w0.name, refs_w0)
                logging.info("wrote %s", dest / w0.name)
            if w1:
                refit.refit_nif(w1, dest / w1.name, refs_w1)
                logging.info("wrote %s", dest / w1.name)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
