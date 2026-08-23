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

"""POST-RECONVERT AUDIT: did this pack regress against the last one?

    python scripts/analysis/postreconvert_audit.py [<output mod dir>]
    python scripts/analysis/postreconvert_audit.py ... --record   # set baseline

Exit 0 clean / 1 a regression / 2 nothing measured.

WHY THIS IS TRACKED AND ITS BASELINE IS A FILE. Every previous version of this
audit lived in the untracked working notes as a DATED FORK
(`postreconvert_audit_2026_08_21.py`, `..._08_22.py`) carrying the previous
run's numbers as HARD-CODED CONSTANTS. The 08-22 copy says so itself: running
the 08-21 one unmodified "would score this pack against a two-generation-old
baseline and call a regression an improvement". A baseline that has to be
hand-copied forward is a baseline that eventually is not.

So the numbers live in `postreconvert_baseline.json` BESIDE THE PACK -- per-pack
state, not source -- and this file holds only the metric list and each metric's
DIRECTION.

IT NEVER RECORDS A BASELINE ON ITS OWN. `--record` is required, because a pack
measured while the converter is still writing it is a half-finished pack, and
silently baselining that would poison every comparison afterwards with numbers
nobody chose.

WHAT IT DOES NOT COVER, said plainly so a clean result is not over-read: order
fidelity and third-party colour-variant bindings need every NIF opened and are
left to their own scripts; the registered-bone invariant is
`registered_bone_audit.py`; which change touched which piece is
`change_attribution.py`. This is the cheap always-run layer, not all of them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

BASELINE_NAME = "postreconvert_baseline.json"

# metric -> (direction, why it matters). LOWER means smaller is better; NEUTRAL
# is reported but never failed on -- pack size legitimately falls when a fix
# works by OMISSION, and calling that a regression is how a good run gets
# reverted.
SPEC = {
    "converted_ok":            ("HIGHER", "mods that converted without a hard failure"),
    "hard_failures":           ("LOWER",  "a mod that did not convert at all"),
    "nif_errors":              ("LOWER",  "meshes the converter could not write"),
    "nif_morph_losses":        ("LOWER",  "pieces that lost their BODYTRI morphs"),
    "load_failures":           ("LOWER",  "source meshes that would not load"),
    "weight_partner_warnings": ("LOWER",  "_0/_1 pairs that diverged"),
    "armor_nifs":              ("NEUTRAL", "meshes converted; falls when a fix omits work"),
    "pack_nifs":               ("NEUTRAL", "meshes present under meshes/!UBE"),
    "physics_xmls":            ("NEUTRAL", "physics configs shipped"),
    "xml_bom_double_encoded":  ("LOWER",  "BUG-12: unparseable, FSMP loads no physics"),
    "xml_unparseable":         ("LOWER",  "10 of these are the AUTHORS' and expected"),
}


def measure(out_mod: Path) -> dict:
    m: dict = {}
    rep = out_mod / "conversion_report.json"
    if rep.is_file():
        try:
            d = json.loads(rep.read_text(encoding="utf-8"))
        except ValueError:
            d = {}
        for k in ("converted_ok", "hard_failures", "nif_errors",
                  "nif_morph_losses", "load_failures", "armor_nifs"):
            if isinstance(d.get(k), int):
                m[k] = d[k]
        wp = d.get("weight_partner_warnings")
        if isinstance(wp, list):
            m["weight_partner_warnings"] = len(wp)
        pf = d.get("pass_failures")
        if isinstance(pf, dict):
            for name, n in pf.items():
                m[f"pass_failure:{name}"] = n
    meshes = out_mod / "meshes"
    if meshes.is_dir():
        m["pack_nifs"] = sum(1 for _ in meshes.rglob("*.nif"))
        xmls = list(meshes.rglob("*.xml"))
        m["physics_xmls"] = len(xmls)
        bad_bom = unparseable = 0
        for x in xmls:
            try:
                b = x.read_bytes()
            except OSError:
                continue
            # BUG-12: a UTF-8 BOM re-encoded through cp1252 lands as mojibake
            # BEFORE the declaration, so the file stops being well-formed.
            head = b[:8]
            if b"<" in b and head[:3] != b"\xef\xbb\xbf" and b.lstrip()[:1] != b"<":
                bad_bom += 1
            try:
                import xml.etree.ElementTree as ET
                ET.fromstring(b.decode("utf-8", "replace"))
            except Exception:
                unparseable += 1
        m["xml_bom_double_encoded"] = bad_bom
        m["xml_unparseable"] = unparseable
    return m


def direction(key: str) -> str:
    if key.startswith("pass_failure:"):
        return "LOWER"
    return SPEC.get(key, ("NEUTRAL", ""))[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    # NO ABSOLUTE DEFAULT. This repository is public, and a hard-coded pack path
    # leaks one person's drive layout to every clone -- the pre-commit hygiene
    # hook rejected exactly that here. Discover it, or take it as an argument.
    ap.add_argument("out_mod", nargs="?", default=None,
                    help="the converter's output mod dir; discovered from the "
                         "configured MO2 instance when omitted")
    ap.add_argument("--record", action="store_true",
                    help="write the CURRENT measurement as the new baseline")
    ap.add_argument("--note", default="",
                    help="provenance for the recorded baseline -- WHICH pack "
                         "and WHY it is trustworthy. A baseline whose origin "
                         "nobody can state is one nobody can defend.")
    args = ap.parse_args()

    if args.out_mod:
        out_mod = Path(args.out_mod)
    else:
        try:
            import os
            from src import paths
            out_mod = (Path(paths.mods_root())
                       / os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto"))
        except Exception as e:
            print(f"could not discover the output mod ({e!r}). Pass it as an "
                  f"argument.", file=sys.stderr)
            return 2
    if not out_mod.is_dir():
        print(f"output mod not found: {out_mod}", file=sys.stderr)
        return 2
    now = measure(out_mod)
    if not now:
        # 0/0 IS NOT A PASS. A missing report plus an empty mesh tree measures
        # nothing, and "no regressions" from that is a claim about an empty set.
        print("NOTHING MEASURED -- no conversion_report.json and no meshes. "
              "This is not a clean result.", file=sys.stderr)
        return 2

    base_p = out_mod / BASELINE_NAME
    base = {}
    if base_p.is_file():
        try:
            base = json.loads(base_p.read_text(encoding="utf-8")).get("metrics", {})
        except ValueError:
            base = {}

    print(f"pack: {out_mod}")
    print(f"baseline: {'none yet' if not base else base_p.name}")
    print(f"\n{'metric':<40}{'baseline':>10}{'now':>10}  verdict")
    print("-" * 92)
    regressions = []
    for key in sorted(set(now) | set(base)):
        b, n = base.get(key), now.get(key)
        d = direction(key)
        if b is None:
            verdict = "NEW"
        elif n is None:
            verdict = "GONE -- metric no longer produced"
        elif b == n:
            verdict = "unchanged"
        elif d == "NEUTRAL":
            verdict = f"changed {b} -> {n} (neutral)"
        else:
            better = (n < b) if d == "LOWER" else (n > b)
            verdict = f"{'IMPROVED' if better else 'REGRESSED'} {b} -> {n}"
            if not better:
                regressions.append((key, b, n))
        print(f"{key:<40}{str(b):>10}{str(n):>10}  {verdict}")

    print("\nNOT COVERED HERE (run these separately, a clean result above is "
          "not a clean pack):")
    print("  registered_bone_audit.py   collider bones the XML never declares")
    print("  change_attribution.py      which change touched which piece")
    print("  order fidelity / colour-variant bindings need every NIF opened")

    if args.record:
        if not args.note:
            print("\nREFUSING to record without --note. A baseline is the thing "
                  "every later\nrun is judged against; if its provenance is not "
                  "written down, the first\nsurprising delta becomes an argument "
                  "about which pack it came from.",
                  file=sys.stderr)
            return 2
        base_p.write_text(
            json.dumps({"note": args.note, "metrics": now}, indent=1),
            encoding="utf-8")
        print(f"\nBASELINE RECORDED -> {base_p}")
        print(f"  note: {args.note}")
        print("  Only do this on a pack you have decided is GOOD. A baseline "
              "taken mid-run\n  poisons every comparison after it.")
        return 0
    if not base:
        print("\nNO BASELINE YET, so nothing above is a verdict. Re-run with "
              "--record once\nthis pack is judged good.")
        return 0
    if regressions:
        print(f"\n{len(regressions)} REGRESSION(S):")
        for k, b, n in regressions:
            print(f"  {k}: {b} -> {n}   ({SPEC.get(k, ('', 'pass failure'))[1]})")
        return 1
    print("\nno regression against the recorded baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
