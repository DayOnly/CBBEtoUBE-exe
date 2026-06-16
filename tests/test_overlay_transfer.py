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

"""Unit tests for the body-overlay -> UBE-UV transfer (pure pieces): the
conservative overlay classifier, TGA round-trip, bilinear sampler, UV->3D
rasterizer, and the texconv locator."""
import numpy as np

from src import overlay_transfer as ot


def test_classify_overlay_path_aware():
    O = "textures/actors/character/overlays/"
    # slot in the FILENAME (Community Overlays convention)
    assert ot.classify_overlay(O + "co/00 body.dds") == "body"
    assert ot.classify_overlay(O + "co/01 hands.dds") == "hands"
    assert ot.classify_overlay(O + "co/02 feet.dds") == "feet"
    assert ot.classify_overlay(O + "co/02 head m.dds") == "head"
    assert ot.classify_overlay(O + "co/warpaint_face.dds") == "head"
    # makeup keywords anywhere in the path -> head (never remapped)
    assert ot.classify_overlay(O + "fms/blush/blush_01.dds") == "head"
    assert ot.classify_overlay(O + "fms/eyeliner/liner.dds") == "head"
    # slot in a FOLDER, not the filename (WNB convention)
    assert ot.classify_overlay(O + "wnb/hand/01.dds") == "hands"
    assert ot.classify_overlay(O + "wnb/face/01.dds") == "head"
    # unlabeled body paint -> 'ambiguous' (resolved at the set level by discover)
    assert ot.classify_overlay(O + "wnb/arcolis/arcolis 1.dds") == "ambiguous"
    assert ot.classify_overlay(O + "fms/extra/extra 1.dds") == "ambiguous"
    assert ot._overlay_set(O + "wnb/arcolis/arcolis 1.dds") == "wnb"
    # body-part NAMES (whole word) -> body (the 'rx' set: rx abs/butt/chest)
    assert ot.classify_overlay(O + "rx/rx butt cat.dds") == "body"
    assert ot.classify_overlay(O + "rx/rx abs butterfly.dds") == "body"
    assert ot.classify_overlay(O + "rx/rx chest dragon.dds") == "body"
    # whole-word matching must NOT false-match (butterfly!=butt, armor!=arm)
    assert ot.classify_overlay(O + "x/butterfly.dds") == "ambiguous"
    assert ot.classify_overlay(O + "x/armor study.dds") == "ambiguous"
    assert ot.classify_overlay(O + "x/background.dds") == "ambiguous"
    # gender prefix fused onto a body part ("malechest") still resolves to body
    assert ot.classify_overlay(O + "mig/mig_malechest_roma.dds") == "body"
    assert ot.classify_overlay(O + "x/femalethigh art.dds") == "body"
    # ...but a fused face part is still face (head keyword wins first)
    assert ot.classify_overlay(O + "x/femalehead freckles.dds") == "head"


def test_set_override_file(tmp_path, monkeypatch):
    """A user overlay_slots.txt forces a region for an otherwise-unclassifiable
    set; 'skip' drops it; the override also beats classification."""
    ot._set_overrides_cache.clear()
    mr = tmp_path / "mods"

    def mk(mod, sub):
        from pathlib import Path
        d = (mr / mod / "textures" / "actors" / "character" / "overlays"
             / Path(sub).parent)
        d.mkdir(parents=True, exist_ok=True)
        (d / Path(sub).name).write_bytes(b"\x00")

    mk("OBI", "obistuff/obimalewow2_1.dds")     # all-unlabeled -> normally skipped
    mk("LAM", "lamenthia/age spots 01.dds")     # all-unlabeled -> normally skipped
    (tmp_path / "overlay_slots.txt").write_text(
        "# user overrides\nobistuff = body\nlamenthia = skip\n", encoding="utf-8")
    monkeypatch.setattr(ot._paths, "mods_root", lambda: mr)
    monkeypatch.setattr(ot._paths, "enabled_mods_ordered",
                        lambda lay: ["OBI", "LAM"])

    by = ot.discover_overlays(None, ("body", "hands", "feet"))
    body = {k.split("/overlays/")[1] for k in by["body"]}
    assert "obistuff/obimalewow2_1.dds" in body        # forced to body
    assert not any("lamenthia" in k for k in body)      # 'skip' -> dropped
    ot._set_overrides_cache.clear()


def test_discover_resolves_ambiguous_by_set(tmp_path, monkeypatch):
    """A body-paint set (one that also has body/hand/feet slots) -> its unmarked
    files become body; an all-makeup set -> its unmarked files stay face/skipped.
    This is what makes folder-organized paints (WNB/Arcolis) transfer while
    makeup sets (FMS/Extra) are left alone."""
    from pathlib import Path
    mr = tmp_path / "mods"

    def mk(mod, sub):                       # sub e.g. "WNB/Hand/h.dds"
        d = (mr / mod / "textures" / "actors" / "character" / "overlays"
             / Path(sub).parent)
        d.mkdir(parents=True, exist_ok=True)
        (d / Path(sub).name).write_bytes(b"\x00")

    mk("WNB", "WNB/Hand/h.dds")             # body-paint set: has a hand slot
    mk("WNB", "WNB/Arcolis/arcolis 1.dds")  # -> this ambiguous file becomes body
    mk("WNB", "WNB/Face/f.dds")             # face -> skipped
    mk("FMS", "FMS/Blush/b.dds")            # all-makeup set
    mk("FMS", "FMS/Extra/e.dds")            # -> this ambiguous file stays face
    # RX: body-paint set recognized via body-part NAMES (no "body" keyword)
    mk("RX", "RX/rx butt cat.dds")          # body-part word -> body
    mk("RX", "RX/rx chestl birds.dds")      # concatenated 'chestl' -> ambiguous
    monkeypatch.setattr(ot._paths, "mods_root", lambda: mr)
    monkeypatch.setattr(ot._paths, "enabled_mods_ordered",
                        lambda lay: ["WNB", "FMS", "RX"])

    by = ot.discover_overlays(None, ("body", "hands", "feet"))
    body = {k.split("/overlays/")[1] for k in by["body"]}
    hands = {k.split("/overlays/")[1] for k in by["hands"]}
    assert "wnb/arcolis/arcolis 1.dds" in body     # body-paint set ambiguous -> body
    assert "wnb/hand/h.dds" in hands
    assert "fms/extra/e.dds" not in body           # makeup set ambiguous stays face
    assert "rx/rx butt cat.dds" in body            # body-part name -> body
    assert "rx/rx chestl birds.dds" in body        # ambiguous, but rx is a body set
    assert not any(("face" in k or "blush" in k) for k in by["body"])


def test_discover_scans_character_assets_overlays_root(tmp_path, monkeypatch):
    """Overlays under the alternate '.../character/character assets/overlays/'
    root (some packs ship there, e.g. a wounds pack) must be discovered too --
    an un-scanned overlay is left in its source UV and lands on the wrong
    anatomy on the UBE body."""
    from pathlib import Path
    mr = tmp_path / "mods"

    def mk(mod, rel):
        d = (mr / mod / Path(rel)).parent
        d.mkdir(parents=True, exist_ok=True)
        (mr / mod / Path(rel)).write_bytes(b"\x00")

    mk("Wounds", "textures/actors/character/character assets/overlays/wounds_arm_right.dds")
    mk("WNB", "textures/actors/character/overlays/wnb/01 body.dds")
    monkeypatch.setattr(ot._paths, "mods_root", lambda: mr)
    monkeypatch.setattr(ot._paths, "enabled_mods_ordered", lambda lay: ["Wounds", "WNB"])

    body = {k.split("/overlays/")[1] for k in ot.discover_overlays(None, ("body",))["body"]}
    assert "wounds_arm_right.dds" in body        # alternate root scanned + body ("arm")
    assert "wnb/01 body.dds" in body             # standard root still works


def test_tga_roundtrip(tmp_path):
    rng = np.arange(8 * 6 * 4, dtype=np.uint8).reshape(8, 6, 4)
    p = tmp_path / "t.tga"
    ot._write_tga_rgba(rng, p)
    back = ot._read_tga_rgba(p)
    assert back.shape == rng.shape
    assert np.array_equal(back, rng)


def test_bilinear_sample_corners_and_center():
    # 2x2 image: distinct corners; sample exact corners + center
    img = np.array([[[0, 0, 0, 0], [255, 0, 0, 255]],
                    [[0, 255, 0, 255], [0, 0, 255, 255]]], np.uint8)
    u = np.array([0.0, 1.0, 0.0, 1.0, 0.5])
    v = np.array([0.0, 0.0, 1.0, 1.0, 0.5])
    s = ot._bilinear_sample(img, u, v)
    assert np.allclose(s[0], [0, 0, 0, 0])          # top-left
    assert np.allclose(s[1], [255, 0, 0, 255])      # top-right
    assert np.allclose(s[2], [0, 255, 0, 255])      # bottom-left
    assert np.allclose(s[3], [0, 0, 255, 255])      # bottom-right
    assert np.allclose(s[4], np.mean(img.reshape(4, 4), axis=0))  # center = avg


def test_rasterize_uv_to_3d_barycentric():
    # one triangle filling the lower-left half of UV; 3D verts encode position
    uv = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    verts = np.array([[0.0, 0, 0], [10.0, 0, 0], [0.0, 10, 0]])
    tris = np.array([[0, 1, 2]])
    T = 8
    pt, cov = ot._rasterize_uv_to_3d(uv, verts, tris, T)
    assert cov.any()
    # a covered texel near UV (0.1,0.1) -> 3D near (1,1,0)
    ys, xs = np.where(cov)
    # pick the texel closest to (0.5,0.5) in UV
    uvpix = np.stack([xs / (T - 1), ys / (T - 1)], 1)
    i = np.argmin(np.abs(uvpix - 0.3).sum(1))
    # barycentric: 3D x ~ 10*u, y ~ 10*v
    assert abs(pt[ys[i], xs[i], 0] - 10 * uvpix[i, 0]) < 2.0
    assert abs(pt[ys[i], xs[i], 1] - 10 * uvpix[i, 1]) < 2.0


def test_find_texconv_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "texconv.exe"
    fake.write_bytes(b"\x00")
    monkeypatch.setenv("CBBE2UBE_TEXCONV", str(fake))
    ot._TEXCONV_CACHE.clear()
    assert ot.find_texconv() == fake
    ot._TEXCONV_CACHE.clear()
