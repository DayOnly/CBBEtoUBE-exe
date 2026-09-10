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

"""Guard for _complete_weight_partners (#180): every converted weighted body
mesh must have BOTH _0 and _1 on disk; the safety net copies the present weight
to any missing partner. Weight-agnostic meshes (no _0/_1) and already-complete
bases are left alone.

#stale-weight-partner (2026-09-06). THE FILL USED TO HAPPEN ONCE, EVER --
the copy was guarded by `if miss.exists(): continue`, so a partner written by
an OLD build was never refreshed. Every later run rewrote the real half and
left the copy alone. Measured on the shipped pack: 11 of 1536 pairs were 12-15
DAYS apart, every one a piece whose source ships only `_1`. It also poisons any
pack-wide census -- 4 of that pack's 6 zero-weight bones sat on those stale
files and read as the current build's defects.

`test_a_real_low_weight_variant_is_never_overwritten` is the load-bearing one.
Refreshing without consulting the SOURCE would copy `_1` over a legitimately
converted `_0` pack-wide, silently deleting the low-weight shape."""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.auto_convert import _complete_weight_partners


def _touch(p: Path, data=b"NIF"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_fills_missing_partner(tmp_path):
    ube = tmp_path / "meshes" / "!UBE"
    # _1-only base (the bug) -> should get a _0 copy
    _touch(ube / "clothes" / "robe" / "robef_1.nif", b"ONE")
    # already-complete base -> untouched
    _touch(ube / "armor" / "x" / "cuirass_0.nif", b"A0")
    _touch(ube / "armor" / "x" / "cuirass_1.nif", b"A1")
    # weight-agnostic mesh (no _0/_1) -> ignored
    _touch(ube / "armor" / "x" / "helmet.nif", b"H")

    filled, refreshed = _complete_weight_partners(tmp_path)
    assert (filled, refreshed) == (1, 0)
    miss = ube / "clothes" / "robe" / "robef_0.nif"
    assert miss.is_file()
    assert miss.read_bytes() == b"ONE"                 # copied from the present _1
    # complete base + weight-agnostic mesh: no spurious files created
    assert not (ube / "armor" / "x" / "helmet_0.nif").exists()
    assert (ube / "armor" / "x" / "cuirass_0.nif").read_bytes() == b"A0"  # untouched


def test_fills_missing_1_from_0(tmp_path):
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "a" / "thing_0.nif", b"ZERO")          # _0-only -> get _1
    assert _complete_weight_partners(tmp_path) == (1, 0)
    assert (ube / "a" / "thing_1.nif").read_bytes() == b"ZERO"


def test_noop_when_all_complete(tmp_path):
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "a" / "b_0.nif"); _touch(ube / "a" / "b_1.nif")
    assert _complete_weight_partners(tmp_path) == (0, 0)


def test_no_ube_dir_is_safe(tmp_path):
    assert _complete_weight_partners(tmp_path) == (0, 0)  # no meshes/!UBE


# ------------------------------------------------- #stale-weight-partner


def test_a_stale_filled_partner_is_refreshed(tmp_path):
    """The defect. The source ships only `_1`, so the `_0` on disk is a copy this
    function made on some earlier run -- and the current `_1` has moved on."""
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "HS" / "cuirass_1.nif", b"NEW-BUILD")
    _touch(ube / "HS" / "cuirass_0.nif", b"AUGUST-BUILD")
    filled, refreshed = _complete_weight_partners(
        tmp_path, source_variants={"hs/cuirass": {"_1"}})
    assert (filled, refreshed) == (0, 1)
    assert (ube / "HS" / "cuirass_0.nif").read_bytes() == b"NEW-BUILD"


def test_a_real_low_weight_variant_is_never_overwritten(tmp_path):
    """THE ONE THAT MATTERS. The source ships BOTH weights, so both outputs are
    real meshes. Copying one over the other would delete the low-weight shape --
    pack-wide, silently. This is why the refresh consults the SOURCE and not the
    mtimes."""
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "armor" / "x" / "cuirass_0.nif", b"REAL-LOW")
    _touch(ube / "armor" / "x" / "cuirass_1.nif", b"REAL-HIGH")
    filled, refreshed = _complete_weight_partners(
        tmp_path, source_variants={"armor/x/cuirass": {"_0", "_1"}})
    assert (filled, refreshed) == (0, 0)
    assert (ube / "armor" / "x" / "cuirass_0.nif").read_bytes() == b"REAL-LOW"


def test_without_source_knowledge_nothing_is_refreshed(tmp_path):
    """No `source_variants` -> the old behaviour exactly. A caller that cannot
    say which half is a fill must not get a guess."""
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "HS" / "cuirass_1.nif", b"NEW")
    _touch(ube / "HS" / "cuirass_0.nif", b"OLD")
    for kw in ({}, {"source_variants": None}, {"source_variants": {}}):
        assert _complete_weight_partners(tmp_path, **kw) == (0, 0), kw
        assert (ube / "HS" / "cuirass_0.nif").read_bytes() == b"OLD"


def test_a_base_the_source_never_mentioned_is_left_alone(tmp_path):
    """Knowledge about OTHER pieces must not be read as knowledge about this one.
    """
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "HS" / "cuirass_1.nif", b"NEW")
    _touch(ube / "HS" / "cuirass_0.nif", b"OLD")
    filled, refreshed = _complete_weight_partners(
        tmp_path, source_variants={"somewhere/else": {"_1"}})
    assert (filled, refreshed) == (0, 0)
    assert (ube / "HS" / "cuirass_0.nif").read_bytes() == b"OLD"


def test_an_already_current_copy_is_not_rewritten(tmp_path):
    """Idempotent: a fill that already matches is left alone, so a reconvert does
    not churn its mtime and the pack stays stable."""
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "HS" / "cuirass_1.nif", b"SAME")
    _touch(ube / "HS" / "cuirass_0.nif", b"SAME")
    filled, refreshed = _complete_weight_partners(
        tmp_path, source_variants={"hs/cuirass": {"_1"}})
    assert (filled, refreshed) == (0, 0)


def test_the_missing_side_is_decided_by_the_source_not_by_which_is_newer(tmp_path):
    """A source that ships only `_0` makes the `_1` the fill, and the refresh has
    to run in that direction too."""
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "a" / "thing_0.nif", b"REAL-ZERO")
    _touch(ube / "a" / "thing_1.nif", b"STALE-ONE")
    filled, refreshed = _complete_weight_partners(
        tmp_path, source_variants={"a/thing": {"_0"}})
    assert (filled, refreshed) == (0, 1)
    assert (ube / "a" / "thing_1.nif").read_bytes() == b"REAL-ZERO"
