"""The Reference bodies dialog's logic, without Tk (src/body_choice.py).

What the dropdown starts on, what each line says, when a choice needs a second
look, and the four overrides the child gets -- the parts of the dialog that
decide which body a conversion actually uses.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import body_choice as bc  # noqa: E402
from src.zeroed_body import BodyCandidate, BodyListing  # noqa: E402


def _pair(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    for w in ("0", "1"):
        (d / f"femalebody_{w}.nif").write_bytes(b"nif")
    return d / "femalebody_1.nif", d / "femalebody_0.nif"


def _cand(tmp_path, provider, status="zeroed", loads=False, kind="cbbe", dev=0.0):
    p1, p0 = _pair(tmp_path, provider)
    return BodyCandidate(kind, provider, p1, p0, loads, status, True,
                         "Set" if status == "zeroed" else "", dev,
                         "" if status == "zeroed" else "not the zeroed build -- off by up to 1.971u")


def _listing(cands, kind="cbbe"):
    default = next((c for c in cands if c.game_loads and c.status == "zeroed"), None)
    return BodyListing(kind, "CBBE 3BA" if kind == "cbbe" else "UBE",
                       tuple(cands), default, ())


def test_the_dropdown_starts_on_the_verified_zeroed_body_the_game_loads(tmp_path):
    out = _cand(tmp_path, "Out", loads=True)
    mod = _cand(tmp_path, "Body Mod", status="not-zeroed", dev=1.971)
    assert bc.initial_choice(_listing([mod, out]), {}) is out


def test_a_usable_body_an_override_names_is_preselected(tmp_path):
    """The dialog never hides a choice already made in the environment."""
    out = _cand(tmp_path, "Out", loads=True)
    mod = _cand(tmp_path, "Body Mod", status="not-zeroed", dev=1.971)
    env = {"CBBE2UBE_CBBE_BODY_1": str(mod.path_1)}
    assert bc.initial_choice(_listing([out, mod]), env) is mod


def test_labels_say_whether_a_body_is_the_verified_zeroed_one(tmp_path):
    out = _cand(tmp_path, "Out", loads=True)
    mod = _cand(tmp_path, "Body Mod", status="not-zeroed", dev=1.971)
    assert bc.label_for(out) == "[zeroed] Out  (the game loads it)"
    assert bc.label_for(mod) == "[NOT zeroed, off by up to 1.97u] Body Mod"


def test_only_the_verified_default_needs_no_second_look(tmp_path):
    out = _cand(tmp_path, "Out", loads=True)
    low = _cand(tmp_path, "Lower", loads=False)
    mod = _cand(tmp_path, "Body Mod", status="not-zeroed", dev=1.971)
    lst = _listing([out, low, mod])
    assert bc.warning_for(out, lst) is None
    assert "the game loads Out" in bc.warning_for(low, lst)
    assert "off by up to 1.971u" in bc.warning_for(mod, lst)
    assert "the converter's own lookup decides" in bc.warning_for(None, lst)


def test_the_child_gets_both_weights_of_both_bodies(tmp_path):
    cbbe = _cand(tmp_path, "Out", loads=True)
    ube = _cand(tmp_path, "Ube", loads=True, kind="ube")
    env = bc.env_for({"cbbe": cbbe, "ube": ube})
    assert env == {"CBBE2UBE_CBBE_BODY_0": str(cbbe.path_0),
                   "CBBE2UBE_CBBE_BODY_1": str(cbbe.path_1),
                   "CBBE2UBE_UBE_BODY_0": str(ube.path_0),
                   "CBBE2UBE_UBE_BODY_1": str(ube.path_1)}


def test_an_unchosen_kind_keeps_no_inherited_override(tmp_path, monkeypatch):
    """A kind left unchosen -- every body refused, say -- used to write
    nothing, so an override inherited from the environment reached the child
    and the converter used the very body the dialog had refused, while the
    dialog said the body would be found by name. Built the way _worker builds
    the child's env, then read by the converter's own lookup."""
    from src import nif_convert_bodyrefs as br
    refused = tmp_path / "femalebody_tangent_1.nif"
    refused.write_bytes(b"nif")
    inherited = {"CBBE2UBE_UBE_BODY_1": str(refused),
                 "CBBE2UBE_UBE_BODY": str(refused)}
    env = dict(inherited)
    env.update(bc.env_for({"cbbe": _cand(tmp_path, "Out", loads=True), "ube": None}))
    assert env["CBBE2UBE_UBE_BODY_1"] == env["CBBE2UBE_UBE_BODY"] == ""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert br._ube_body_override("_1") is None


def test_overrides_read_the_settings_picker_as_its_weight1_file(tmp_path):
    got = bc.overrides({"CBBE2UBE_UBE_BODY": str(tmp_path / "femalebody_tangent_0.nif")})
    assert got == [("ube", "Settings: UBE body reference NIF",
                    str(tmp_path / "femalebody_tangent_1.nif"))]


def test_an_override_is_labelled_with_the_variable_it_came_from(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    only0 = bc.overrides({"CBBE2UBE_CBBE_BODY_0": str(a / "femalebody_0.nif")})
    assert only0 == [("cbbe", "override CBBE2UBE_CBBE_BODY_0", str(a / "femalebody_1.nif"))]
    split = bc.overrides({"CBBE2UBE_CBBE_BODY_1": str(a / "femalebody_1.nif"),
                          "CBBE2UBE_CBBE_BODY_0": str(b / "femalebody_0.nif")})
    assert [lab for _k, lab, _p in split] == ["override CBBE2UBE_CBBE_BODY_1",
                                              "override CBBE2UBE_CBBE_BODY_0"], \
        "a weight-0 override in another folder must not be folded into the weight-1 one"
    same = bc.overrides({"CBBE2UBE_CBBE_BODY_1": str(a / "femalebody_1.nif"),
                         "CBBE2UBE_CBBE_BODY_0": str(a / "femalebody_0.nif")})
    assert len(same) == 1


def test_two_equal_lines_are_told_apart_by_their_folder(tmp_path):
    """The pick is read back by its text: two identical lines (two game Data
    folders) pinned the FIRST one's files whichever was chosen."""
    d1 = _cand(tmp_path, "one", loads=False)
    d2 = _cand(tmp_path, "two", loads=False)
    same = [BodyCandidate(**{**c.__dict__, "provider": "game Data folder"}) for c in (d1, d2)]
    labels = bc.labels_for(same)
    assert len(set(labels)) == 2
    assert str(d1.path_1.parent) in labels[0] and str(d2.path_1.parent) in labels[1]
    assert bc.labels_for([d1, d2]) == [bc.label_for(d1), bc.label_for(d2)]


def test_the_texts_under_the_dropdown_and_in_the_run_log(tmp_path):
    out = _cand(tmp_path, "Out", loads=True)
    mod = _cand(tmp_path, "Body Mod", status="not-zeroed", dev=1.971)
    assert "Verified: BodySlide's zeroed build of 'Set'" in bc.detail_for(out)
    assert bc.detail_for(mod).endswith("off by up to 1.971u")
    assert bc.detail_for(None, 2).startswith("Nothing selected: pick a body above")
    assert bc.detail_for(None, 0).startswith("No usable body was found")
    lines = bc.summary_lines({"cbbe": out, "ube": None})
    assert lines == ["  CBBE body: Out -- verified zeroed build",
                     "  UBE body: none chosen -- " + bc._OWN_LOOKUP]
    assert bc.summary_lines({"cbbe": mod, "ube": None})[0].endswith("NOT verified (not-zeroed)")
    refused = BodyCandidate("cbbe", "Other", None, None, False, "other-family", False,
                            "", None, "a 7-vertex body, not CBBE 3BA (6)")
    assert bc.refused_lines(_listing([out, refused])) == [
        "Other: a 7-vertex body, not CBBE 3BA (6)"]


def test_the_confirmation_names_an_override_this_run_will_not_use(tmp_path):
    out = _cand(tmp_path, "Out", loads=True)
    ube = _cand(tmp_path, "Ube", loads=True, kind="ube")
    mine = _cand(tmp_path, "Mine", kind="ube", status="not-zeroed", dev=1.0)
    env = {"CBBE2UBE_UBE_BODY": str(mine.path_1)}
    assert bc.replaced({"cbbe": out, "ube": ube}, env) == [
        f"CBBE2UBE_UBE_BODY = {mine.path_1} is not used for this run"]
    assert bc.replaced({"cbbe": out, "ube": mine}, env) == []
    assert bc.replaced({"cbbe": out, "ube": None}, env) != []
    assert bc.replaced({"cbbe": out, "ube": ube}, {}) == []


def test_a_chosen_file_that_vanished_is_caught_before_launch(tmp_path):
    out = _cand(tmp_path, "Out", loads=True)
    out.path_0.unlink()
    assert bc.missing_files({"cbbe": out}) == [str(out.path_0)]
