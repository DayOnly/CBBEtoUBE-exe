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
# line. Vulture treats every name touched here as "used". tests/test_no_dead_code.py
# runs
#
#     python -m vulture src scripts cbbe_to_ube_main.py vulture_whitelist.py --min-confidence 60
#
# and fails on any finding under src/: code there must be reached from src/, the
# entry point or a script -- a test alone does not keep it alive. #dead-code-ratchet
#
# A NEW dead symbol that isn't in this file will still surface. Do NOT add a symbol
# here to silence it without proving it is genuinely intentional -- that defeats the
# tool. And when you DELETE a symbol, delete its line here in the same change: an
# entry for a name that no longer exists is a standing exemption that can only ever
# hide a future real finding under that name. (This paragraph used to say the
# opposite -- "harmless (vulture ignores unknown names here)" -- while the note at
# the end of the next section said the true thing. Three stale entries were living
# in the gap; they were removed 2026-09-09.)

# ---- Public/symmetric API kept for completeness (no live caller today) ----
_.nif_converted     # one leg of the converted/skipped/errors result triple (siblings live)
clear_load_cache    # companion to esp's cached loader; asymmetric to drop just the clear
_.offsets_dict      # OsdMorph dict-view accessor -- public parser API
_.by_name           # OsdFile name-index accessor -- public parser API

# ---- Documented design knowledge / staged features (deliberately unwired) ----
# `FEMINIZE_MALE_ARMOR`, `SHADER_TYPE_DEFAULT` and `SHADER_FLAGS_1_ENV_MAPPING_BIT`
# REMOVED 2026-09-09, for the reason the note below already gives: all three were
# deleted from the tree (ba73f12, 87daf2a) BEFORE this file's own last edit, which
# removed `_strip_alpha_property` on exactly that reasoning and missed these.
# `_strip_alpha_property` REMOVED from this whitelist 2026-08-23: the function
# was deleted in 682284f, so the entry suppressed a name that no longer exists,
# and its stated justification ("cross-ref'd in-code") had stopped being true
# too -- the single comment referencing it misdescribed what it did. A whitelist
# entry for a deleted symbol is not harmless: it is a standing exemption that
# can only ever hide a FUTURE real finding under the same name.

# ---- False positives: written but not read back (schema fields / struct writes) ----
cli                 # Setting dataclass field, set via constructor kwargs
advanced            # Setting dataclass field, set via constructor kwargs
shape_locations     # result-dataclass field, populated for downstream/debug use
_.interpolatorID    # NIF controller field -- assignment writes into the NIF structure
_.nextControllerID  # NIF controller field -- assignment writes into the NIF structure
_._shader           # pynifly shape shader handle -- assignment persists to the NIF
_.targetID          # NIF controller field -- assignment writes into the NIF structure
_._weights          # pynifly shape weight cache -- set to None so the next read sees the written buffer
_.dwLength          # ctypes MEMORYSTATUSEX field -- GlobalMemoryStatusEx reads it
_.LimitFlags        # ctypes job-object field -- SetInformationJobObject reads it

# ---- Read only by the suite: a contract table or counter the tests pin ----
FIT_STAGES          # fit-chain contract table; tests/test_fit_stage_table.py holds both chains to it
THEME_KEYS          # theme schema; tests/test_gui_themes.py checks every theme defines exactly these
_.rebuilds          # worker-pool rebuild counter; test_pair_unit_dispatch and test_robustness_audit_2026_06_22 assert on it
BREAST_APEX_Z       # body-zone landmark (z of the breast apex); pinned by tests/test_body_zones.py
UPPER_CHEST_Z       # named so nobody reaches for it as the breast band; pinned by tests/test_body_zones.py
plan_weight_writes  # pure model of the weight-write rule nif_convert_weights applies; tests pin it

# ---- Test scaffolding (pytest / synthetic-NIF fixtures use these by framework) ----
pytestmark          # module-level pytest marker, read by pytest not by our code
_.interpolation
_.forward
_.backward
_.frequency
_.stopTime
_.shutdown_called
