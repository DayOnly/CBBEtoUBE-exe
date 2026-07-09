"""Convert ONE armor's mesh pair (interpreted path) for fast diagnose/fix/verify
loops on a single piece -- without a full-pack reconvert. Resolves biped slots
from the mod's ESP (ARMA whose MOD3 references the stem, non-male) and the UBE
body ref automatically. Recipe flags come from the environment, so wrap the call:

  CBBE2UBE_THIGH_STANDOFF=1.0 python scripts/convert_one_armor.py \
      "D:/Modlists/ARR/mods/<Mod>" narmor/leathersuitn dcuirass  C:/tmp/out

Args: <mod_dir> <mesh_subdir-under-meshes> <stem> [out_dir]
Then: python scripts/armor_clip_diag.py <out_dir>/<stem>_1.nif <mod>/.../<stem>_1.nif
"""
import os, sys, struct
from pathlib import Path

REPO = r"C:\Users\Sam\Downloads\cbbe-to-ube"
sys.path.insert(0, str(Path(REPO, ".pynifly").resolve()))
sys.path.insert(0, REPO)
os.environ.setdefault("CBBE2UBE_MO2_INI", r"D:\Modlists\ARR\ModOrganizer.ini")

from src import paths, esp as E, auto_convert as ac   # noqa: E402
import src.nif_convert as nc                            # noqa: E402


def biped_slots_for(mod_dir, stem):
    """First non-male ARMA whose MOD3 references <stem>.nif -> its BOD2/BODT slots."""
    for espp in Path(mod_dir).glob("*.esp"):
        try:
            e = E.ESP.load(espp)
        except Exception:
            continue
        for g in e.groups:
            if g.label != b"ARMA":
                continue
            for r in g.records:
                m3 = None
                b2 = 0
                for s, d in E.iter_subrecords(r.payload):
                    if s == b"MOD3":
                        m3 = d.rstrip(b"\x00").decode("latin1", "ignore").lower()
                    if s in (b"BOD2", b"BODT") and len(d) >= 4:
                        b2 = struct.unpack_from("<I", d, 0)[0]
                if m3 and f"{stem.lower()}" in m3 and "\\m\\" not in m3:
                    return b2
    return 0


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    mod_dir, subdir, stem = sys.argv[1:4]
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path(os.environ["TEMP"], "one_armor")
    out.mkdir(parents=True, exist_ok=True)
    paths.export_to_env(paths.discover_layout())
    slots = biped_slots_for(mod_dir, stem)
    ref = str(ac._find_ube_body_ref())
    srcd = Path(mod_dir, "meshes", subdir)
    for w in ("_0", "_1"):
        src = srcd / f"{stem}{w}.nif"
        if not src.exists():
            print(f"  MISSING {src}")
            continue
        r = nc.convert_nif(str(src), str(out / f"{stem}{w}.nif"),
                           ube_body_ref_path=ref, biped_slots=slots)
        print(f"  {stem}{w}: {getattr(r, 'status', r)}  slots=0x{slots:x} -> {out}")


if __name__ == "__main__":
    main()
