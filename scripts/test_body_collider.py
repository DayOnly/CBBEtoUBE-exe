"""OPTION 5 TEST: give the UBE body its own HDT-SMP per-triangle collider so
cloth tagged `can-collide-with body` collides against the body instead of
clipping. Scoped to UBE-body actors (it's the UBE body mesh). Stages a modified
femalebody_tangent_0/1.nif + the collision XML for in-game testing."""
import os, sys, shutil, tempfile
from pathlib import Path

os.environ["CBBE2UBE_MODS_ROOT"] = r"D:\Modlists\ARR\mods"
REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from src import nif_convert as nc          # noqa: E402
from src import hdt_xml_gen as hx          # noqa: E402
from pyn import pynifly                     # noqa: E402
from pyn.pynifly import NiStringExtraData   # noqa: E402

STAGE = Path(tempfile.mkdtemp(prefix="bodycol_"))
HDT_NAME = "HDT Skinned Mesh Physics Object"
# the collision XML lives next to the body NIF; _0/_1 share it
XML_REL = Path("meshes") / "!UBE" / "Body" / "femalebody_tangent.xml"
XML_REF = "Meshes\\!UBE\\Body\\femalebody_tangent.xml"


def main():
    b0 = nc._find_ube_femalebody("_0")
    b1 = nc._find_ube_femalebody("_1")
    print("UBE body _0:", b0)
    print("UBE body _1:", b1)
    nf = pynifly.NifFile(str(b1))
    main = next(s for s in nf.shapes if s.name == "BaseShape")
    bones = sorted(main.bone_names)
    print(f"BaseShape bones: {len(bones)}")

    # 1) generate the body collider XML (per-triangle 'body' tag, kinematic bones)
    xml = hx.generate_body_collision_xml(body_shape_name="BaseShape",
                                         bone_names=bones)
    xml_disk = STAGE / XML_REL
    xml_disk.parent.mkdir(parents=True, exist_ok=True)
    xml_disk.write_text(xml, encoding="utf-8")

    # 2) validate against the body's bones/shapes
    errs = hx.validate_armor_hdt_xml(xml_disk, bones)
    print("XML validation:", errs if errs else "OK")

    # 3) copy each weight's body NIF, attach the HDT ref, save
    for src, suf in ((b0, "_0"), (b1, "_1")):
        if not src:
            print(f"  {suf}: source missing, skip"); continue
        dst = STAGE / "meshes" / "!UBE" / "Body" / f"femalebody_tangent{suf}.nif"
        shutil.copy(src, dst)
        n = pynifly.NifFile(str(dst))
        if not any(getattr(ed, "name", None) == HDT_NAME
                   for ed in n.rootNode.extra_data()):
            NiStringExtraData.New(n, name=HDT_NAME, string_value=XML_REF,
                                  parent=n.rootNode)
        n.save()
        # verify the ref round-tripped
        chk = pynifly.NifFile(str(dst))
        got = [ed.string_data for ed in chk.rootNode.extra_data()
               if getattr(ed, "name", None) == HDT_NAME]
        shapes = [s.name for s in chk.shapes]
        print(f"  {suf}: ref={got}  shapes={shapes}")
    print("\nXML head:")
    print("\n".join(xml.splitlines()[:14]))
    print("\nSTAGE:", STAGE)


if __name__ == "__main__":
    main()
