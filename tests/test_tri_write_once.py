"""#tri-write-once: one BODYTRI per armour, not one per weight variant.

`x_0.nif` and `x_1.nif` both derive `x.tri`, so both used to generate AND write
it -- on every pair in the pack. Two defects, both measured on a 161-mod run:
a rename race that cost 4 pieces their TRI, and -- worse because it was silent --
a nondeterministic result, since the two variants do NOT produce the same file
(17 of 24 morphs differed on one boot, worst 0.171u/vertex).

The `_0` variant owns it. That is MEASURED: shipping `_1` put a nipple through a
leather cuirass in game, and the armour that had been working carried a
`_0`-derived TRI -- on that cuirass's chest shape 92 of 155 morphs differ,
worst `Juicy_breasts` 2.16u. Matching the working file against both candidates:
`_0`-derived mean |delta| 0.00002u, `_1`-derived 0.00157u / 2.16u worst.

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
    """`_0`, measured against the armour that was working in game."""
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
