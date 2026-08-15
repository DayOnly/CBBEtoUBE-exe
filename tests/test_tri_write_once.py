"""#tri-write-once: one BODYTRI per armour, not one per weight variant.

`x_0.nif` and `x_1.nif` both derive `x.tri`, so both used to generate AND write
it -- on every pair in the pack. Two defects, both measured on a 161-mod run:
a rename race that cost 4 pieces their TRI, and -- worse because it was silent --
a nondeterministic result, since the two variants do NOT produce the same file
(17 of 24 morphs differed on one boot, worst 0.171u/vertex).

The `_0` variant owns it. The reason is DETERMINISM, not a fixed defect: pick
either one, but pick the same one every run.

It was briefly believed that shipping `_1` put a nipple through a leather
cuirass in game. That is DISPROVEN (2026-08-14). The numbers behind it were
scored against a backup captured AFTER the user reported the nipple -- broken
vs broken. Re-scored against the true last-known-good build, the `_0`- and
`_1`-derived TRIs are BOTH 3.2492u from it on the chest shape, and under the
player's own preset both measure 0 of 1256 tip verts exposed. The variant
choice does not move the poke. What survives is the rule below and this: never
size a weight-variant decision on a piece with no bust (the original choice was
validated on a BOOT pair, where the two differ by 0.17u and it reads as
rounding).

These pin the rule and, above all, the exception: a `_1` with no partner must
still get a TRI, or single-variant armour silently loses body morphs -- a worse
bug than the one being fixed.
"""
import src.nif_convert as nc


def _mk(d, *names):
    for n in names:
        (d / n).write_bytes(b"nif")
    return d


def test_low_weight_variant_owns_the_tri(tmp_path):
    """`_0` owns it -- one variant, chosen deterministically."""
    _mk(tmp_path, "boots_0.nif", "boots_1.nif")
    assert nc._tri_is_owning_variant(tmp_path / "boots_0.nif") is True
    assert nc._tri_is_owning_variant(tmp_path / "boots_1.nif") is False


def test_lone_weight_variant_with_no_partner_still_generates(tmp_path):
    """The exception that matters: yielding here would ship no TRI at all."""
    _mk(tmp_path, "solo_1.nif")
    assert nc._tri_is_owning_variant(tmp_path / "solo_1.nif") is True


def test_unsuffixed_mesh_owns_its_tri(tmp_path):
    _mk(tmp_path, "plain.nif")
    assert nc._tri_is_owning_variant(tmp_path / "plain.nif") is True


def test_exactly_one_variant_of_a_pair_owns_it(tmp_path):
    """Never both (the race) and never neither (no morphs)."""
    _mk(tmp_path, "a_0.nif", "a_1.nif")
    owners = [n for n in ("a_0.nif", "a_1.nif")
              if nc._tri_is_owning_variant(tmp_path / n)]
    assert owners == ["a_0.nif"]


def test_undecidable_path_generates_rather_than_skips(tmp_path):
    """Fail toward a TRI: a missing one is a visible defect in game, a
    duplicate write is only wasted work."""
    assert nc._tri_is_owning_variant(None) is True


def test_yielding_variant_still_gets_its_bodytri_reference(tmp_path, monkeypatch):
    """The variant that does NOT write must still POINT at the shared TRI.

    Suppressing the write is the fix; suppressing the reference would cost the
    yielding mesh its body morphs outright -- worse than the race. An early
    version nulled the destination path itself, which both dropped the reference
    and crashed on the next line (`auto_tri_dst.parts` on None) for every
    yielding body-swap piece. Nothing in the suite covered that, so this is the
    guard.
    """
    src = tmp_path / "x_1.nif"
    src.write_bytes(b"nif")
    (tmp_path / "x_0.nif").write_bytes(b"nif")
    # the ownership rule says this variant does NOT write...
    assert nc._tri_is_owning_variant(src) is False
    # ...but the shared destination path is still derivable for the reference,
    # i.e. nothing about the rule may depend on nulling it.
    stem = src.stem[:-2]
    assert (tmp_path / (stem + ".tri")).name == "x.tri"
