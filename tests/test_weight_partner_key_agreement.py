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

"""#stale-weight-partner, the PRODUCER/CONSUMER SEAM.

`tests/test_weight_partner.py` proves the refresh logic, but every one of its
cases HAND-WRITES the `source_variants` dict. Production does not: the keys are
minted by `_variant_sources_by_base` from the SOURCE-relative mesh path, and
`_complete_weight_partners` looks them up by a key it derives from the
DESTINATION path relative to `meshes/!UBE`. Those are two different strings put
through `_weight_base_key` at two different points in the batch.

Nothing pinned that they agree. If either side ever re-keyed -- a `meshes/`
prefix kept on one side, the `!UBE` segment left in, a case change -- all six
refresh tests would still pass and the refresh would go SILENTLY INERT in
production: `src_sufs` comes back `None` for every base and the function takes
its `continue`. That is the same failure shape as the first
`#tri-variant-collision` fix, which was inert on the real pack while its unit
tests passed, and it would be invisible except as stale `_0`s quietly surviving
another 15 days.

So this walks the REAL path: build `resolved_pairs` the way the batch does,
mint the map with the REAL producer, reduce it to suffixes exactly as
`auto_convert` does, and hand THAT to the real consumer.
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.auto_convert import (        # noqa: E402
    _complete_weight_partners,
    _variant_sources_by_base,
    _weight_base_key,
)


def _touch(p: Path, data=b"NIF"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _suffix_map(resolved_pairs):
    """Exactly what `auto_convert` does at the producer end (the
    `source_weight_variants` reduction, then the cross-mod union)."""
    out: dict = {}
    for base, by_suffix in _variant_sources_by_base(resolved_pairs).items():
        out.setdefault(base, set()).update(by_suffix)
    return out


def test_producer_key_matches_consumer_key(tmp_path):
    """THE SEAM. A source that ships only `_1`, a stale `_0` on disk from an
    older build, and the map built by the real producer -- the refresh must
    fire. If the two ends ever disagree this is the only test that fails."""
    src = tmp_path / "src"
    rel = "HS/cuirass_1.nif"
    _touch(src / rel, b"SOURCE")
    resolved_pairs = [(src / rel, rel)]

    ube = tmp_path / "out" / "meshes" / "!UBE"
    _touch(ube / "HS" / "cuirass_1.nif", b"NEW-BUILD")
    _touch(ube / "HS" / "cuirass_0.nif", b"AUGUST-BUILD")

    variants = _suffix_map(resolved_pairs)
    # The mask must select something -- an empty map would make the assertion
    # below pass for the wrong reason on the `filled` side.
    assert variants == {"hs/cuirass": {"_1"}}, variants

    filled, refreshed = _complete_weight_partners(
        tmp_path / "out", source_variants=variants)
    assert (filled, refreshed) == (0, 1)
    assert (ube / "HS" / "cuirass_0.nif").read_bytes() == b"NEW-BUILD"


def test_the_two_ends_agree_on_every_shape_of_path():
    """The key is derived from a SOURCE-relative path on one side and a
    DEST-relative one on the other. Pin the normalisations that have to cancel:
    a redundant `meshes/` prefix, backslashes, case, and the weight suffix."""
    for src_rel, dst_rel in [
        ("HS/cuirass_1.nif", "HS/cuirass_0.nif"),
        ("meshes/HS/cuirass_1.nif", "HS/cuirass_0.nif"),
        (r"HS\cuirass_1.nif", "HS/cuirass_0.nif"),
        ("HS/Cuirass_1.nif", "hs/cuirass_0.nif"),
        ("a/b/c/thing_0.nif", "a/b/c/thing_1.nif"),
    ]:
        produced = set(_variant_sources_by_base([(Path(src_rel), src_rel)]))
        consumed = _weight_base_key(dst_rel)
        assert produced == {consumed}, (src_rel, dst_rel, produced, consumed)


def test_a_source_shipping_both_weights_yields_both_suffixes():
    """The guard that stops the refresh deleting a real low-weight mesh is the
    suffix SET, so the producer has to report both when the source ships both."""
    pairs = [(Path("armor/x/cuirass_0.nif"), "armor/x/cuirass_0.nif"),
             (Path("armor/x/cuirass_1.nif"), "armor/x/cuirass_1.nif")]
    assert _suffix_map(pairs) == {"armor/x/cuirass": {"_0", "_1"}}


def test_an_already_ube_source_is_not_credited_as_a_variant():
    """`_variant_sources_by_base` drops already-UBE models. If one leaked in as
    a `_0`, the map would claim the source ships both weights and the refresh
    would never fire for that base."""
    pairs = [(Path("!UBE/HS/cuirass_0.nif"), "!UBE/HS/cuirass_0.nif"),
             (Path("HS/cuirass_1.nif"), "HS/cuirass_1.nif")]
    assert _suffix_map(pairs) == {"hs/cuirass": {"_1"}}
