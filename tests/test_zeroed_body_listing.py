"""list_bodies(): every body the Reference bodies dialog can offer, each checked.

The dialog must show the user every enabled provider of each body -- not only
the one the game loads -- with an honest status, and start on exactly the body
zeroed_body() would return. Bodies of another family, half-installed pairs and
unreadable files are listed but not selectable.
"""
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import zeroed_body as zb  # noqa: E402
from tests.test_zeroed_body import BODY_DIR, PRESET, ZEROED, Modlist  # noqa: E402


def _listing(ml, order, **kw):
    return zb.list_bodies(("cbbe",), mods_root=ml.root, order=order, **kw)["cbbe"]


def test_every_provider_is_listed_and_the_default_is_what_zeroed_body_returns(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Body Mod", PRESET)
    ml.body("My BodySlide Output - 3BA", ZEROED)
    order = ["My BodySlide Output - 3BA", "Body Mod"]
    lst = _listing(ml, order)
    assert [c.provider for c in lst.candidates] == ["My BodySlide Output - 3BA", "Body Mod"]
    out, mod = lst.candidates
    assert (out.status, out.game_loads, out.selectable) == ("zeroed", True, True)
    assert (mod.status, mod.game_loads, mod.selectable) == ("not-zeroed", False, True)
    assert mod.max_dev is not None and abs(mod.max_dev - 2.0) < 1e-6
    assert "off by up to 2.000u" in mod.reason
    assert lst.default is out
    for w, p in (("_1", out.path_1), ("_0", out.path_0)):
        assert str(zb.zeroed_body("cbbe", w, mods_root=ml.root, order=order).path).lower() \
            == str(p).lower()


def test_no_default_when_the_game_loads_a_preset_build(tmp_path, monkeypatch):
    """A lower-priority zeroed body is offered, never made the default: the
    default is what the GAME loads, verified."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Body Mod", PRESET)
    ml.body("Out", ZEROED)
    lst = _listing(ml, ["Body Mod", "Out"])
    assert lst.default is None
    assert [c.status for c in lst.candidates] == ["not-zeroed", "zeroed"]


def test_other_families_and_half_pairs_are_listed_but_not_offered(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", ZEROED)
    ml.body("Half", ZEROED, weights=("_1",))
    seven = {w: np.vstack([v, [[9.0, 9.0, 9.0]]]) for w, v in ZEROED.items()}
    ml.body("Other Body", seven)
    lst = _listing(ml, ["Out", "Half", "Other Body", "Body Mod"])
    by = {c.provider: c for c in lst.candidates}
    assert by["Half"].status == "half-pair" and not by["Half"].selectable
    assert by["Other Body"].status == "other-family" and not by["Other Body"].selectable
    assert "7-vertex body, not CBBE 3BA" in by["Other Body"].reason
    assert by["Out"].selectable


def test_each_slider_set_is_built_once_per_weight(tmp_path, monkeypatch):
    """The expensive part is shared across every candidate checked."""
    ml = Modlist(tmp_path, monkeypatch)
    for mod in ("A", "B", "C"):
        ml.body(mod, ZEROED)
    calls = []
    real = zb._zeroed_build
    monkeypatch.setattr(zb, "_zeroed_build",
                        lambda vfs, ss, w: calls.append(w) or real(vfs, ss, w))
    lst = _listing(ml, ["A", "B", "C", "Body Mod"])
    assert len(lst.candidates) == 3
    assert sorted(calls) == ["_0", "_1"]


def test_a_pair_zeroed_at_one_weight_only_is_not_zeroed(tmp_path, monkeypatch):
    """Both weights are compared: a weight-1-only check called this pair the
    zeroed default while zeroed_body('_0') refuses it."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", {"_1": ZEROED["_1"], "_0": PRESET["_0"]})
    lst = _listing(ml, ["Out", "Body Mod"])
    (out,) = lst.candidates
    assert out.status == "not-zeroed" and out.game_loads
    assert lst.default is None


def test_the_game_loads_a_pair_only_if_both_weights_come_from_it(tmp_path, monkeypatch):
    """A higher mod shipping only weight 0 splits the pair the game loads: the
    zeroed pair underneath is offered, never the default."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("A", ZEROED)
    ml.body("B", PRESET, weights=("_0",))
    lst = _listing(ml, ["B", "A", "Body Mod"])
    a = next(c for c in lst.candidates if c.provider == "A")
    assert a.status == "zeroed" and not a.game_loads
    assert lst.default is None


def _zap_at_weight0(ml):
    from tests.test_zeroed_body import _osp, _set
    ml.slider_sets("Body Mod", _osp(_set(
        extra='<Slider name="Zap" zap="true" small="100" big="0"></Slider>')))


def test_a_weight_that_cannot_be_checked_does_not_hide_a_measured_mismatch(tmp_path, monkeypatch):
    """Weight 1 measured 2.0u off, weight 0 unbuildable (a zap on by default):
    it used to come back 'unchecked' with only the weight-0 reason, and be
    preselected as the game-loaded fallback."""
    ml = Modlist(tmp_path, monkeypatch)
    _zap_at_weight0(ml)
    ml.body("Out", PRESET)
    (out,) = _listing(ml, ["Out", "Body Mod"]).candidates
    assert out.status == "not-zeroed"
    assert "weight 1:" in out.reason and "off by up to 2.000u" in out.reason
    assert "weight 0:" in out.reason and "could not be checked" in out.reason
    assert out.max_dev is None, "an unchecked weight is not covered by 'up to'"


def test_a_body_that_cannot_be_checked_is_offered_but_never_the_default(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    _zap_at_weight0(ml)
    ml.body("Out", ZEROED)
    lst = _listing(ml, ["Out", "Body Mod"])
    (out,) = lst.candidates
    assert (out.status, out.selectable) == ("unchecked", True)
    assert out.reason.startswith("cannot be checked: weight 0:")
    assert lst.default is None


def test_nothing_measured_is_not_reported_as_a_deviation(tmp_path, monkeypatch):
    """A same-size body whose shape has another name compares nothing; it read
    '[NOT zeroed, off by up to 0.00u]' -- near perfect, from no measurement."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Renamed", ZEROED, shape="Body2")
    (c,) = _listing(ml, ["Renamed", "Body Mod"]).candidates
    assert c.status == "not-zeroed" and c.max_dev is None
    assert "has no 'Body' shape" in c.reason


def test_an_extra_bigger_shape_does_not_make_a_body_another_family(tmp_path, monkeypatch):
    """Family is decided the way zeroed_body() decides: by the body shape, not
    by whichever shape in the file is largest."""
    ml = Modlist(tmp_path, monkeypatch)
    for w in ("_0", "_1"):
        ml.nif(ml.root / "Out" / BODY_DIR / f"femalebody{w}.nif",
               Body=ZEROED[w], Big=np.zeros((40, 3)))
    order = ["Out", "Body Mod"]
    lst = _listing(ml, order)
    assert lst.default is not None and lst.default.provider == "Out"
    assert zb.zeroed_body("cbbe", "_1", mods_root=ml.root, order=order).slider_set


def test_an_override_naming_no_file_says_so(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", ZEROED)
    gone = tmp_path / "gone" / "femalebody_1.nif"
    lst = _listing(ml, ["Out", "Body Mod"],
                   extra=[("cbbe", "override CBBE2UBE_CBBE_BODY_1", str(gone))])
    c = lst.candidates[-1]
    assert (c.status, c.selectable) == ("missing", False)
    assert c.reason == "names a file that does not exist"


def test_a_body_named_without_a_weight_is_one_file_for_both(tmp_path, monkeypatch):
    """The converter uses such a file as-is at both weights (the Settings UBE
    picker allows it); the dialog refused it as a half-installed pair and put
    the default in its place without a word."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", ZEROED)
    one = tmp_path / "picked" / "femalebody.nif"
    ml.nif(one, Body=ZEROED["_1"])
    lst = _listing(ml, ["Out", "Body Mod"], extra=[("cbbe", "Settings: picker", str(one))])
    c = lst.candidates[-1]
    assert c.selectable and c.path_1 == c.path_0
    assert c.status == "not-zeroed"                   # weight 0 differs
    assert c.reason.endswith("one file, used at both weights")


def test_an_override_naming_a_listed_provider_is_not_listed_twice(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", ZEROED)
    p1 = ml.root / "Out" / BODY_DIR / "femalebody_1.nif"
    lst = _listing(ml, ["Out", "Body Mod"],
                   extra=[("cbbe", "override CBBE2UBE_CBBE_BODY_1", str(p1))])
    assert [c.provider for c in lst.candidates] == ["Out"]


def test_a_body_an_override_names_is_listed_and_checked(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", ZEROED)
    elsewhere = tmp_path / "picked"
    ml.body(None, PRESET, where=elsewhere)
    p1 = elsewhere / BODY_DIR / "femalebody_1.nif"
    lst = _listing(ml, ["Out", "Body Mod"],
                   extra=[("cbbe", "override CBBE2UBE_CBBE_BODY_1", str(p1))])
    extra = lst.candidates[-1]
    assert extra.provider == "override CBBE2UBE_CBBE_BODY_1"
    assert extra.status == "not-zeroed" and extra.selectable
    assert lst.default.provider == "Out"
