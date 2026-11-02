"""#extremity-slot-gate: a Hands/Feet-named shape is body skin to swap for the UBE extremity only where the piece
hides that extremity (slot 33 hands / 37 feet) and only when the physics XML does not use it as a collider or a
soft body. Unknown slots keep the name rule."""
from src import nif_convert as nc

S33, S37 = nc.BIPED_SLOT33_BIT, nc.BIPED_SLOT37_BIT
SKIRT = (1 << (47 - 30)) | (1 << (52 - 30)) | (1 << (56 - 30))


def test_feet_in_boots_is_swapped():
    assert nc._is_extremity_skin_here("Feet", S37)


def test_hands_in_gauntlets_is_swapped():
    assert nc._is_extremity_skin_here("Hands", S33)


def test_feet_named_shape_in_a_skirt_is_not_foot_skin():
    assert not nc._is_extremity_skin_here("feet", SKIRT)


def test_hands_named_shape_in_boots_is_not_hand_skin():
    assert not nc._is_extremity_skin_here("Hands", S37)


def test_physics_collider_named_feet_is_never_foot_skin():
    assert not nc._is_extremity_skin_here("feet", S37, collider_names={"feet"})
    assert not nc._is_extremity_skin_here("Feet", S37, softbody_names={"Feet"})


def test_unknown_slots_keep_the_name_rule():
    assert nc._is_extremity_skin_here("Feet", 0)
    assert not nc._is_extremity_skin_here("Cuirass", 0)
