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

"""registered_bone_audit must not write into the modlist it measures.

Its default pack is `<mods>/CBBEtoUBE Auto/meshes/!UBE` -- inside the MO2
instance -- and until 2026-09-21 every run wrote `registered_bone_audit.json`
beside it, before the verdict printed. Now it writes only to an explicit
`--out PATH`.

These run the REAL script end to end (runpy) against a stand-in instance on
disk: the real layout discovery resolves the default pack from a fake
ModOrganizer.ini, the real `output_nifs` enumerates it, the real VFS lookup
pairs each piece with its source. Only the NIF reader, the skeleton and the
physics-XML readers are stubbed. The instance is fingerprinted before and after,
and each run must reach its VERDICT line -- a run that died early would write
nothing too, and prove nothing.
"""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(_REPO))

from pyn import pynifly  # noqa: E402
from src import paths  # noqa: E402
from tests import _converter_sources as _cs  # noqa: E402

SCRIPT = _REPO / "scripts" / "analysis" / "registered_bone_audit.py"
DECLARED = "NPC Spine2 [Spn2]"
ADDED = "NPC Root [Root]"
PIECES = ("armor/robe/robe_1.nif", "armor/robe/robe_0.nif")


class _Shape:
    def __init__(self, name, bones):
        self.name = name
        self.bone_weights = {b: [] for b in bones}


def _instance(tmp_path, monkeypatch, *, with_source=True, added=True):
    """A modlist on disk: the pack, one source mod, a profile. Returns
    (instance dir, default pack dir)."""
    inst = tmp_path / "instance"
    mods = inst / "mods"
    pack = mods / "CBBEtoUBE Auto" / "meshes" / "!UBE"
    for rel in PIECES:
        (pack / rel).parent.mkdir(parents=True, exist_ok=True)
        (pack / rel).write_bytes(b"nif")
        if with_source:
            src = mods / "Source Mod" / "meshes" / rel
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(b"nif")
    (inst / "profiles" / "Default").mkdir(parents=True)
    (inst / "profiles" / "Default" / "modlist.txt").write_text(
        "+CBBEtoUBE Auto\n+Source Mod\n", encoding="utf-8")
    (inst / "ModOrganizer.ini").write_text(
        "[General]\nselected_profile=Default\n", encoding="utf-8")

    monkeypatch.setenv(paths.MO2_INI_ENV, str(inst / "ModOrganizer.ini"))
    monkeypatch.setenv(paths.MODS_ROOT_ENV, str(mods))
    monkeypatch.setenv(paths.GAME_DATA_ENV, "")   # recorded, so the script's
    monkeypatch.delenv(paths.GAME_DATA_ENV)       # export_to_env is undone
    monkeypatch.setattr(sys, "path", list(sys.path))

    ours = [DECLARED, ADDED] if added else [DECLARED]

    def nif(filepath):
        f = type("Nif", (), {})()
        f.shapes = ([_Shape("VirtualGround", ours)] if "!UBE" in filepath
                    else [_Shape("Body", [DECLARED])])
        return f

    monkeypatch.setattr(pynifly, "NifFile", nif)
    _cs.patch(monkeypatch, "_actor_skeleton_bone_parents",
              lambda: {f"bone{i}": "root" for i in range(150)})
    _cs.patch(monkeypatch, "_hdt_collider_shape_names",
              lambda p, nif=None: ["VirtualGround"])
    _cs.patch(monkeypatch, "_hdt_softbody_shape_names", lambda p, nif=None: [])
    _cs.patch(monkeypatch, "_read_source_hdt_xml_text",
              lambda p, nif=None: f'<bone name="{DECLARED}"/>')
    _cs.patch(monkeypatch, "_nearest_declared_ancestor", lambda b, d, o: None)
    return inst, pack


def _fingerprint(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()}


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *argv])
    with pytest.raises(SystemExit) as ex:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    return ex.value.code, capsys.readouterr().out


def test_a_default_run_writes_nothing_into_the_instance(tmp_path, monkeypatch, capsys):
    inst, pack = _instance(tmp_path, monkeypatch)
    before = _fingerprint(inst)
    code, out = _run(monkeypatch, capsys)
    assert "VERDICT: 2 shape(s) carry a bone WE added" in out, out
    assert "over 2 piece(s) checked" in out, out
    assert code == 1
    assert _fingerprint(inst) == before, \
        "a default run changed the modlist instance it was measuring"
    assert not (pack.parent.parent / "registered_bone_audit.json").exists()


def test_out_writes_the_rows_there_and_only_there(tmp_path, monkeypatch, capsys):
    inst, _ = _instance(tmp_path, monkeypatch)
    before = _fingerprint(inst)
    dest = tmp_path / "elsewhere" / "rows.json"
    dest.parent.mkdir()
    code, out = _run(monkeypatch, capsys, "--out", str(dest))
    assert code == 1 and "VERDICT: 2 shape(s)" in out, out
    rows = json.loads(dest.read_text(encoding="utf-8"))
    assert sorted(r["nif"].replace("\\", "/") for r in rows) == sorted(PIECES)
    assert all(r["added"] == [ADDED] and r["kind"] == "SHAPE WE CREATED"
               for r in rows)
    assert _fingerprint(inst) == before


def test_an_explicit_pack_and_ini_still_parse_ahead_of_out(tmp_path, monkeypatch, capsys):
    """The positional form every caller used keeps working next to --out."""
    inst, pack = _instance(tmp_path, monkeypatch)
    dest = tmp_path / "rows.json"
    code, out = _run(monkeypatch, capsys, str(pack), str(inst / "ModOrganizer.ini"),
                     "--out", str(dest))
    assert code == 1 and "over 2 piece(s) checked" in out, out
    assert len(json.loads(dest.read_text(encoding="utf-8"))) == 2


@pytest.mark.parametrize("with_source,added,want", [
    (True, False, 0),     # clean: every bone we carry is declared
    (False, True, 2),     # nothing measured: no source to diff against
])
def test_the_gate_codes_are_kept(tmp_path, monkeypatch, capsys, with_source, added, want):
    inst, _ = _instance(tmp_path, monkeypatch, with_source=with_source, added=added)
    before = _fingerprint(inst)
    code, _ = _run(monkeypatch, capsys)
    assert code == want
    assert _fingerprint(inst) == before
