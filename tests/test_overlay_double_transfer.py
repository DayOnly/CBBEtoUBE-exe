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

"""discover_overlays must NOT scan the converter's own output mod. It's the
highest-priority mod, so on a re-run a previous run's already-converted UBE-UV
overlays would win as the "source" and be transferred a SECOND time -> the
double-warped / garbled overlays seen in-game."""
from src import overlay_transfer as ot

REL = "textures/actors/character/overlays/set/01 body.dds"


def _mk(mr, modname):
    d = mr / modname / "textures" / "actors" / "character" / "overlays" / "set"
    d.mkdir(parents=True)
    (d / "01 body.dds").write_bytes(b"\x00")


def test_discover_overlays_excludes_output_mod(tmp_path, monkeypatch):
    mr = tmp_path / "mods"
    _mk(mr, "CBBEtoUBE Auto")   # our output (a prior run's converted overlay)
    _mk(mr, "SourcePaints")     # the real CBBE source
    monkeypatch.setattr(ot._paths, "mods_root", lambda: mr)
    # highest-priority first -> output mod ahead of the source (the real setup)
    monkeypatch.setattr(ot._paths, "enabled_mods_ordered",
                        lambda lay: ["CBBEtoUBE Auto", "SourcePaints"])

    # WITHOUT the skip, the output mod wrongly wins as its own source (the bug).
    by = ot.discover_overlays(None, ("body",))
    assert by["body"][REL][-1] == "CBBEtoUBE Auto"

    # WITH the skip, the real source wins -> a single (correct) transfer.
    by2 = ot.discover_overlays(None, ("body",), skip_mods={"CBBEtoUBE Auto"})
    assert by2["body"][REL][-1] == "SourcePaints"
    # case-insensitive
    by3 = ot.discover_overlays(None, ("body",), skip_mods={"cbbetoube auto"})
    assert by3["body"][REL][-1] == "SourcePaints"
