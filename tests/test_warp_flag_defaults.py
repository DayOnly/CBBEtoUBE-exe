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

"""The two warp guards were decided TOGETHER and came out DIFFERENTLY.

Both had shipped opt-in and off while every in-game verdict on this project was
given on a build that had them forced ON -- so production output differed from
everything anyone had actually looked at, by up to 1.38u on one piece.

Measured per flag, on absolute surface irregularity of the vertices each one
touched (NOT on the deviation-from-ring-mean that the outlier cap optimises,
which would be circular):

  WARP_DELTA_OUTLIER   acts hardest where the defect is worst -- a cuirass skirt's
                       worst spike 10.335 -> 6.439, plus four more shapes improved.
                       Costs are ~0.03 where it does not help. ON.

  WARP_PUSH_SHELL_CAP  touches ZERO verts on every shape of that cuirass with a
                       large spike, regresses Corset and HeavyFur, and still moves
                       geometry 1.38u. OFF.

This file pins both, because "they were only ever off by accident" is exactly the
argument that would flip the second one too.
"""
import inspect

from src import nif_convert as nc


def _decl(name):
    src = inspect.getsource(nc)
    i = src.index(f"{name} = os.environ.get(")
    return src[i:i + 200]


def test_the_outlier_cap_is_ON_by_default():
    assert nc.WARP_DELTA_OUTLIER is True
    d = _decl("WARP_DELTA_OUTLIER")
    assert '"CBBE2UBE_NO_WARP_DELTA_OUTLIER"' in d, (
        "a default-ON flag takes a NO_* kill-switch, matching the convention "
        "CHAIN_ANCHOR_RECREATE and the layer guard already use")
    assert "not in (" in d


def test_the_shell_cap_is_OFF_and_the_measurement_is_why():
    """NOT a hypothetical risk -- it was measured and it did not earn its cost."""
    assert nc.WARP_PUSH_SHELL_CAP is False
    d = _decl("WARP_PUSH_SHELL_CAP")
    assert '"CBBE2UBE_WARP_PUSH_SHELL_CAP"' in d, (
        "must stay an opt-IN: it is inert on every large spike measured and "
        "regresses two shapes, while moving geometry by 1.38u")
    assert "not in (" not in d


def test_the_asymmetry_is_recorded_next_to_the_flag():
    """The reason must travel with the code, or the next reader re-litigates it.

    Specifically the numbers that decide it: the spike the outlier cap fixes is
    ~100x the roughness it adds where it does not help, and the shell cap's own
    figure is that it touched nothing where the spikes were.
    """
    src = inspect.getsource(nc)
    i = src.index("WARP_DELTA_OUTLIER = os.environ.get(")
    why = src[max(0, i - 2200):i]
    assert "10.335" in why and "6.439" in why, (
        "the improvement that earned the default must be quoted at the flag")
    assert "ZERO verts" in why or "zero verts" in why, (
        "the reason the sibling flag stays OFF must be recorded here too, "
        "since the two were decided in one measurement")
