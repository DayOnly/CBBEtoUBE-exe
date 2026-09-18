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

# Cap the BLAS thread pools before ANY module of this package can import numpy.
# #blas-thread-cap. The product's two entry points already cap (the frozen entry
# script, and auto_convert above its nif_convert import), but a script, census or
# test that did `from src import nif_convert` imported numpy uncapped and paid the
# whole OpenBLAS arena: 1,519.9 MB of private commit for that one import on a
# 24-thread box, against 42.7 MB through auto_convert (2026-09-15). This file runs
# before every `src.*` import, so one call covers them all; blas_env imports
# nothing but `os`.
from .blas_env import cap_blas_threads as _cap_blas_threads

_cap_blas_threads()
