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

"""#silent-fallback-count (audit 2026-10-20 F17): the fallbacks that downgrade a pass to its non-adaptive form say so
in the run report instead of passing in silence."""
import inspect

from src import nif_convert as nc


def test_each_named_fallback_is_counted():
    src = inspect.getsource(nc._fit_shapes_swap)
    for label in ("#hf-reskin-fallback", "#conform-amp-fallback", "#antipoke-amp-fallback",
                  "#antipoke-diff-fallback", "#antipoke-jiggle-fallback"):
        assert f'_note_pass_effect("{label}"' in src, label
