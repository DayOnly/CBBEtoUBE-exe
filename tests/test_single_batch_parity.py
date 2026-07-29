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

"""#single-vs-batch-parity -- the single-piece harness must produce what a real
batch run produces, or every number taken from it describes a mesh nobody ships.

MEASURED DIVERGENCE, 2026-07-29. Same piece, same flags, old harness vs the
batch's own output: one shape moved 223/755 verts by up to 3.6u, another
365/2495 by up to 1.9u, bone weights differed by up to 0.25, and the physics-XML
and BODYTRI paths baked into the NIF differed. Three causes, all fixed:

  * slots -- the harness hand-rolled its own ARMA scan over the mod's own ESPs
    and produced 0 for a BodySlide-output mod (no ESP), which silently disables
    every slot-gated pass. It now uses the batch's own slot map + resolver.
  * output root -- the batch writes meshes/!UBE/<subdir>; the harness wrote
    meshes/<subdir>, changing the `Meshes\\...` rel path stored in the NIF.
  * alt-texture shape names -- passed by the batch, not by the harness, so the
    morph-cap merge could treat colour-variant shapes differently.

After the fix the same comparison is bit-identical in geometry and extra data;
the only residual is a single vertex whose 5-influence row broke the
4-influence cap tie differently (both valid, both summing to 1.0).

These tests pin the STRUCTURE -- one conversion path, shared resolvers -- so the
harness cannot quietly grow a second pipeline again.
"""
import ast
import inspect
from pathlib import Path

import pytest

from src import auto_convert as ac

_HARNESS = Path(__file__).resolve().parent.parent / "scripts" / "convert_one_armor.py"


@pytest.fixture(scope="module")
def src_text():
    return _HARNESS.read_text(encoding="utf-8")


def test_harness_dispatches_through_the_batch_worker(src_text):
    """The single conversion path. If the harness ever calls convert_nif
    directly again, it can drift from the batch on any argument the worker
    fills in."""
    assert "_nif_convert_worker(item)" in src_text, (
        "the harness must hand its work item to auto_convert's OWN worker")
    assert "nc.convert_nif(" not in src_text and "convert_nif(" not in src_text, (
        "calling convert_nif directly reintroduces a second pipeline")


def test_work_item_matches_what_the_worker_unpacks(src_text):
    """The worker unpacks (src, dst, ube_ref, slots[, alt_tex]); the harness
    must build exactly that, in that order."""
    worker = inspect.getsource(ac._nif_convert_worker)
    assert "item[:4]" in worker and "item[4]" in worker
    tree = ast.parse(src_text)
    tuples = [n for n in ast.walk(tree)
              if isinstance(n, ast.Tuple) and len(n.elts) == 5]
    assert tuples, "expected a 5-element work-item tuple in the harness"


def test_slots_use_the_shared_resolver_not_a_private_scan(src_text):
    """#slot0-weight-partner: the batch's resolver folds `_0` onto its `_1`
    partner's slots. A private ARMA scan cannot be kept in step with it, and
    the one that existed returned 0 for every ESP-less mod."""
    assert "build_nif_slot_map" in src_text
    assert "_make_slot_resolver" in src_text
    assert "def biped_slots_for" not in src_text, (
        "the hand-rolled slot scan is what produced slots=0; it must stay gone")


def test_zero_slots_is_an_error_not_a_warning(src_text):
    """A slots=0 run silently skips every slot-gated pass and is not
    comparable to production -- it has produced two false findings."""
    i = src_text.index("if not slots:")
    tail = src_text[i:i + 1400]
    assert "ERROR" in tail and "sys.exit(2)" in tail, (
        "unresolved slots must abort, not warn and convert anyway")


def test_output_mirrors_the_batch_ube_prefix(src_text):
    """The batch writes meshes/!UBE/<subdir>; the rel path stored in the NIF's
    physics-XML and BODYTRI extra data is derived from it."""
    assert '"!UBE"' in src_text
    assert "no_ube_prefix" in src_text, "the opt-out must be explicit"
    batch = inspect.getsource(ac)
    assert 'ube_path_prefix: str = "!UBE"' in batch, (
        "batch default changed -- update the harness to match")


def test_alt_texture_shapes_are_collected_like_the_batch(src_text):
    assert "collect_alt_texture_shape_names" in src_text


def test_meshes_ancestor_is_preserved_by_default(src_text):
    """`_read_source_hdt_xml_text` resolves the physics XML by walking up to a
    directory literally named `meshes`; without one it returns None, the
    collider set comes back EMPTY, and every collider protection no-ops."""
    assert '"meshes"' in src_text
    # anchor on the FLAG DECLARATION, not the usage line in the docstring
    i = src_text.index('ap.add_argument("--flat"')
    assert "DIAGNOSTIC ONLY" in src_text[i:i + 400], (
        "the no-`meshes`-ancestor layout must be marked as the broken one")
