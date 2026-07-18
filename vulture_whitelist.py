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

# Vulture whitelist: symbols that are unused BY REFERENCE but intentionally kept.
# Each was hand-verified during the 2026-07 dead-code audit; the reason is on the
# line. Vulture treats every name touched here as "used", so a clean run is:
#
#     python -m vulture src tests scripts cbbe_to_ube_main.py vulture_whitelist.py
#
# A NEW dead symbol that isn't in this file will still surface. Removing a symbol
# from the codebase and forgetting its whitelist line is harmless (vulture ignores
# unknown names here). Do NOT add a symbol here to silence it without proving it is
# genuinely intentional -- that defeats the tool.

# ---- Public/symmetric API kept for completeness (no live caller today) ----
_.nif_converted     # one leg of the converted/skipped/errors result triple (siblings live)
clear_load_cache    # companion to esp's cached loader; asymmetric to drop just the clear
_.offsets_dict      # OsdMorph dict-view accessor -- public parser API
_.by_name           # OsdFile name-index accessor -- public parser API

# ---- Documented design knowledge / staged features (deliberately unwired) ----
FEMINIZE_MALE_ARMOR             # wiring switch for feminize_male_armor_conform (tested, parked)
SHADER_TYPE_DEFAULT             # documents the Shader_Type=0 fix for NioOverride morphing
SHADER_FLAGS_1_ENV_MAPPING_BIT  # documents the 0x80 env-map bit that blocks morphing
_strip_alpha_property           # empirical NiAlphaProperty/BodyMorph finding; cross-ref'd in-code

# ---- False positives: written but not read back (schema fields / struct writes) ----
cli                 # Setting dataclass field, set via constructor kwargs
advanced            # Setting dataclass field, set via constructor kwargs
shape_locations     # result-dataclass field, populated for downstream/debug use
_.interpolatorID    # NIF controller field -- assignment writes into the NIF structure
_.nextControllerID  # NIF controller field -- assignment writes into the NIF structure
_._shader           # pynifly shape shader handle -- assignment persists to the NIF

# ---- Test scaffolding (pytest / synthetic-NIF fixtures use these by framework) ----
pytestmark          # module-level pytest marker, read by pytest not by our code
_.interpolation
_.forward
_.backward
_.frequency
_.stopTime
_.shutdown_called
