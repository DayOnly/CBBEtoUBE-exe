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

"""Pure-logic tests for the multi-slot FEET overlay pass (the parts that don't
need the Papyrus compiler / mods / texconv): the feet-UV path scheme, the
AddFeetPaint repoint rewrite, and the compiler locator's env override. The
end-to-end recompile+transfer is verified separately (needs the toolchain)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import overlay_transfer as ot          # noqa: E402
from src import overlay_slots as osl            # noqa: E402


def test_feet_variant_path_inserts_suffix():
    assert ot._feet_variant_path("textures/x/b.dds") == "textures/x/b_ubefeet.dds"
    # backslashes preserved (rewritten verbatim into the .psc); ext normalized
    # to lowercase .dds (Windows is case-insensitive)
    assert ot._feet_variant_path(r"textures\x\B.DDS") == r"textures\x\B_ubefeet.dds"
    # defensive: a path with no .dds extension still gets a distinct name
    assert ot._feet_variant_path("noext") == "noext_ubefeet"


def test_repoint_feet_script_only_multislot():
    # only the body-reused (multi-slot) feet path is repointed; a feet-only
    # AddFeetPaint is left exactly as-is (the region pass handles that one).
    body = "textures/actors/character/overlays/body.dds"
    feet = "textures/actors/character/overlays/feetonly.dds"
    text = f'AddFeetPaint("A", "{body}")\nAddFeetPaint("B", "{feet}")\n'
    out = ot._repoint_feet_script(text, {osl.normalize_script_texpath(body)})
    assert "body_ubefeet.dds" in out
    assert "feetonly.dds" in out
    assert "feetonly_ubefeet" not in out


def test_repoint_feet_script_matches_backslash_literal():
    # .psc paths use backslashes; the rewrite must still match via the same
    # normalization the slot map uses.
    raw = r"Textures\Actors\Character\Overlays\B.dds"
    text = f'AddFeetPaint("T", "{raw}")'
    out = ot._repoint_feet_script(text, {osl.normalize_script_texpath(raw)})
    assert "B_ubefeet.dds" in out


def test_repoint_feet_script_noop_when_not_listed():
    text = 'AddFeetPaint("A", "textures/x/b.dds")\n'
    assert ot._repoint_feet_script(text, set()) == text   # nothing to repoint


def test_find_papyrus_compiler_env(tmp_path, monkeypatch):
    fake = tmp_path / "PapyrusCompiler.exe"
    fake.write_text("x")
    monkeypatch.setenv("CBBE2UBE_PAPYRUS_COMPILER", str(fake))
    assert ot.find_papyrus_compiler() == fake


def test_find_papyrus_compiler_never_raises(monkeypatch):
    monkeypatch.delenv("CBBE2UBE_PAPYRUS_COMPILER", raising=False)
    r = ot.find_papyrus_compiler()
    assert r is None or hasattr(r, "is_file")
