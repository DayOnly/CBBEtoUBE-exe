"""An `auto` run skips body mods by what they SHIP, not by which body the fit
uses (#body-mod-exclusion).

The exclusion used to be the folders of the three body lookups, so it moved
with the reference body: once the fit used BodySlide's zeroed build, the 3BA
body mod itself fell out of it and its collision-body NIFs entered All-mods
runs (140 mods listed instead of 139 on a real modlist); and a Reference bodies
pick changed which mods converted, while the GUI's mod list -- built in the GUI
process, without the pick -- could not show it.
"""
import inspect
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import auto_convert as ac  # noqa: E402

_CBBE = "meshes/actors/character/character assets"


def _ship(mods, mod, rel):
    p = mods / mod / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"nif")
    return p


def test_every_folder_shipping_a_race_body_is_a_body_mod(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    _ship(mods, "Body Mod", f"{_CBBE}/femalebody_1.nif")
    _ship(mods, "BodySlide Output", f"{_CBBE}/femalebody_0.nif")
    _ship(mods, "UBE Output", "meshes/!UBE/Body/femalebody_tangent_1.nif")
    armour = _ship(mods, "Armor Mod", "meshes/armor/x/cuirass_1.nif")
    _ship(mods, "NPC Body", f"{_CBBE}/npc/femalebody_1.nif")   # not the race body
    # a picked body pointing into the armour mod must not change the answer
    monkeypatch.setenv("CBBE2UBE_CBBE_BODY_1", str(armour))
    assert ac._body_mod_names(mods) == {"Body Mod", "BodySlide Output", "UBE Output"}


def test_the_run_and_the_guis_mod_list_exclude_the_same_body_mods():
    for fn in (ac._cmd_auto, ac.list_convertible_mods):
        src = inspect.getsource(fn)
        assert "exclude = {output.name} | _body_mod_names(mr)" in src, fn.__name__
        assert "_find_cbbe_base_body(\"_1\"),\n" not in src, \
            f"{fn.__name__}: the exclusion must not follow the body the fit uses"
