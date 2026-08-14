"""A mod name must not veto positive ARMA evidence.

`_NONSOURCE_NAME_HINTS` carried "khajiit" to exclude beast-race body/fur mods.
It is a substring match on the FOLDER NAME, so it also excluded every khajiit
ARMOUR mod -- reported in game as "all khajiiti armor is invisible", because the
armour records still got a UBE armature minted while their meshes were never
converted.

Measured over a live 161-mod list: 35 enabled mods matched a beast-race hint and
33 had ZERO player-armour ARMA bases, so `require_arma` drops those without any
help from the name. The hint was redundant where it was right and wrong where it
was not -- the same finding that retired the "ube" hint before it.
"""
from src import auto_convert as ac


def test_our_own_output_is_excluded_by_name_whatever_it_contains():
    """These must stay HARD: converting our own output, or the BodySlide output
    the VFS resolves through, is a feedback loop no evidence can justify."""
    for n in ("CBBEtoUBE Auto", "Authoria - Bodyslide Output - 3BA",
              "Dynamic Fur Morph"):
        assert any(h in n.lower() for h in ac._NONSOURCE_NAME_HINTS_HARD), n


def test_beast_race_hints_are_not_hard():
    """They must not be able to veto evidence."""
    for h in ac._NONSOURCE_NAME_HINTS_BEAST:
        assert h not in ac._NONSOURCE_NAME_HINTS_HARD


def test_khajiit_armour_mod_is_not_blocked_by_its_name():
    n = "VickusDickus' Khajiiti Apex Armory Reforged".lower()
    assert any(h in n for h in ac._NONSOURCE_NAME_HINTS_BEAST), \
        "precondition: this name does match a beast-race hint"
    assert not any(h in n for h in ac._NONSOURCE_NAME_HINTS_HARD), \
        "a khajiit ARMOUR mod must not be hard-excluded on its name"


def test_scan_preview_still_uses_every_hint():
    """`scan` does no ESP parse, so it has no evidence to weigh and the name is
    all it has. The union must stay intact for it."""
    for h in ac._NONSOURCE_NAME_HINTS_BEAST + ac._NONSOURCE_NAME_HINTS_HARD:
        assert h in ac._NONSOURCE_NAME_HINTS
