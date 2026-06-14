"""Reconvert the source HDT iron cuirass with the CURRENT code (collider-keep
fix) and verify the output:
  1. exactly ONE body shape (UBE BaseShape ~29298v), no leftover inline body
  2. the HDT colliders (Collision / Belt Col / Bag Col) are KEPT
  3. those colliders stay textureless (won't render as blobs)
  4. the generated HDT XML has NO dangling refs (every referenced shape exists)
"""
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402
from src import nif_convert  # noqa: E402

SRC = Path(r"D:\Modlists\ARR\mods\HDT-SMP Vanilla Armors\meshes\armor\iron\f\cuirasslight_1.nif")


def main():
    ube_body = nif_convert._find_ube_femalebody("_1") or Path(
        r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA\meshes\!UBE\Body\femalebody_tangent_1.nif")
    print("UBE body ref:", ube_body)
    out = Path(tempfile.mkdtemp()) / "cuirasslight_1.nif"
    res = nif_convert.convert_nif(
        SRC, out,
        ube_body_ref_path=ube_body,
        biped_slots=0x4,  # slot 32 (body)
    )
    print("convert status:", res.status, "| reason:", res.reason)
    if not out.is_file():
        print("!! no output NIF written"); return

    nif = pynifly.NifFile(str(out))
    shapes = {s.name: s for s in nif.shapes}
    print(f"\noutput shapes ({len(shapes)}):")
    body_like = []
    for name, s in shapes.items():
        try:
            v = len(s.verts)
        except Exception:
            v = "?"
        tex = {k: val for k, val in (s.textures or {}).items() if val}
        print(f"   {name!r:22} verts={v:<7} tex={list(tex.keys()) or 'NONE'}")
        if isinstance(v, int) and v > 20000:
            body_like.append((name, v))

    print("\n-- checks --")
    print("body shapes (>20k verts):", body_like,
          "=> EXPECT exactly 1 (UBE BaseShape)")
    for col in ("Collision", "Belt Col", "Bag Col"):
        present = col in shapes
        textureless = present and not {k: v for k, v in (shapes[col].textures or {}).items() if v}
        print(f"collider {col!r}: present={present}  textureless={textureless}")
    leftover = [n for n in shapes if "underwearbody" in n.lower()
                or n in ("3BA", "3BA_Anus", "3BA_Vagina")]
    print("leftover inline-body shapes:", leftover, "=> EXPECT []")

    xmlp = out.with_name("cuirasslight.xml")
    print("\nHDT XML:", xmlp, "exists=", xmlp.is_file())
    if xmlp.is_file():
        txt = xmlp.read_text(errors="ignore")
        refs = set(re.findall(
            r'<per-(?:vertex|triangle)-shape\s+name="([^"]+)"', txt))
        dangling = sorted(refs - set(shapes))
        print("  XML shape refs:", sorted(refs))
        print("  DANGLING (referenced but NOT in NIF):", dangling,
              "=> EXPECT []")


if __name__ == "__main__":
    main()
