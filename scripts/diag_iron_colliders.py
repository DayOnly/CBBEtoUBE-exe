"""Inspect the source HDT iron cuirass: per-shape render flags / textures /
verts, and the HDT XML's per-vertex (cloth) vs per-triangle (collider) split.
Decides whether collider shapes are safe to KEEP (hidden, won't render as a
blob) or whether we must instead PRUNE the dangling XML refs."""
import re
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

SRC_NIF = Path(r"D:\Modlists\ARR\mods\HDT-SMP Vanilla Armors\meshes\armor\iron\f\cuirasslight_1.nif")
SRC_XML = SRC_NIF.with_suffix(".xml")
# fallbacks for the XML if not beside the nif
XML_CANDS = [SRC_XML,
             SRC_NIF.parent / "cuirasslight.xml"]


def dump_shape(s):
    name = s.name
    try:
        verts = len(s.verts)
    except Exception:
        verts = "?"
    tex = {}
    try:
        tex = s.textures or {}
    except Exception:
        pass
    nonempty_tex = {k: v for k, v in tex.items() if v}
    # render flags: NiAVObject flags bit0 = hidden in many tools
    flags = getattr(s, "flags", None)
    shader_flags1 = shader_flags2 = shader_type = None
    try:
        sp = s.shader
        shader_type = getattr(sp, "shaderflags_type", getattr(sp, "Shader_Type", None))
        shader_flags1 = getattr(sp, "Shader_Flags_1", None)
        shader_flags2 = getattr(sp, "Shader_Flags_2", None)
    except Exception:
        pass
    bones = []
    try:
        bones = list(s.bone_names)
    except Exception:
        pass
    print(f"  shape={name!r}")
    print(f"      verts={verts}  flags={flags}  shader_type={shader_type}")
    print(f"      textures(nonempty)={list(nonempty_tex.keys()) or 'NONE'}")
    print(f"      shaderflags1={shader_flags1} shaderflags2={shader_flags2}")
    print(f"      bones({len(bones)})={bones[:8]}{'...' if len(bones) > 8 else ''}")


def main():
    print("SOURCE NIF:", SRC_NIF, "exists=", SRC_NIF.is_file())
    nif = pynifly.NifFile(str(SRC_NIF))
    print(f"\n{len(nif.shapes)} shapes:")
    for s in nif.shapes:
        dump_shape(s)

    xmlp = next((c for c in XML_CANDS if c.is_file()), None)
    print("\nXML:", xmlp)
    if xmlp:
        txt = xmlp.read_text(errors="ignore")
        pv = re.findall(r'<per-vertex-shape\s+name="([^"]+)"', txt)
        pt = re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt)
        bones = re.findall(r'<bone\s+name="([^"]+)"', txt)
        print("  per-vertex-shape (CLOTH/soft):", pv)
        print("  per-triangle-shape (COLLIDERS):", pt)
        print("  <bone> chains:", bones)


if __name__ == "__main__":
    main()
