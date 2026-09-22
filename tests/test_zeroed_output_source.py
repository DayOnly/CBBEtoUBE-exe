"""#zeroed-output-source: a piece is converted from the user's BodySlide build
when that build is verified to be the armour's zeroed 3BA build -- and only
then; every other case keeps today's source.

The motivating case (2026-09-22): an armour mod's own loose meshes were made for
the vanilla body; the source tiers picked them over the BodySlide output, whose
file was the zeroed 3BA build to 0.000u. The fit starts from the zeroed CBBE
body, so it kept a 2.6u-looser bust and a 1.0u-tighter inner thigh as the
author's gap. The garment check uses real slider-set XML and OSD files; only
NIF reading is stubbed.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import discovery  # noqa: E402
from src import nif_convert_bodyrefs as br  # noqa: E402
from src import zeroed_body as zb  # noqa: E402
from src.osd import OsdFile, OsdMorph  # noqa: E402

STEM = "armor/test/cuirass"
CUIRASS = np.array([[float(i), 1.0, 0.0] for i in range(4)])
PANTS = np.array([[float(i), -1.0, 0.0] for i in range(5)])
MORPHS = {                                    # name -> [(vertex, dx, dy, dz)]
    "CuirassSeam": [(0, 0.0, 0.0, 1.0)],      # default small=100 big=0 -> weight 0 only
    "CuirassBust": [(2, 0.0, 2.0, 0.0)],      # default 0/0 -> only a PRESET moves it
    "PantsZap": [(3, 0.0, 0.0, 0.0)],         # zap, on at both weights -> deletes vertex 3
    "PantsPast": [(1, 0.5, 0.0, 0.0), (9, 9.0, 9.0, 9.0)],   # vertex 9 is past the shape
}


def _data(morph, target):
    return (f'<Data name="{morph}" target="{target}" local="true">'
            f'Test Armor.osd\\{morph}</Data>')


def _osp(extra=""):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SliderSetInfo version="1">
  <SliderSet name="Test Armor">
    <DataFolder>Test Armor</DataFolder>
    <SourceFile>Test Armor.nif</SourceFile>
    <OutputPath>meshes\\armor\\test</OutputPath>
    <OutputFile GenWeights="true">cuirass</OutputFile>
    <Shape target="Cuirass">Cuirass</Shape>
    <Shape target="Pants">Pants</Shape>
    <Slider name="Seam" invert="false" small="100" big="0">{_data("CuirassSeam", "Cuirass")}</Slider>
    <Slider name="Bust" invert="false" small="0" big="0">{_data("CuirassBust", "Cuirass")}</Slider>
    <Slider name="Zap" invert="false" zap="true" small="100" big="100">{_data("PantsZap", "Pants")}</Slider>
    <Slider name="Past" invert="false" small="100" big="100">{_data("PantsPast", "Pants")}</Slider>
    {extra}
  </SliderSet>
</SliderSetInfo>
"""


def _zeroed(weight, bust=False):
    c, p = CUIRASS.copy(), PANTS.copy()
    if weight == "_0":
        c[0] += (0.0, 0.0, 1.0)
    if bust:
        c[2] += (0.0, 2.0, 0.0)
    p[1] += (0.5, 0.0, 0.0)                    # vertex 9 of that morph is skipped
    return {"Cuirass": c, "Pants": np.delete(p, 3, axis=0)}


class Instance:
    """A mods tree: the armour mod's BodySlide project, the user's BodySlide
    output, and the shapes each fake NIF holds."""

    def __init__(self, root: Path, monkeypatch, osp=None):
        self.mods = root / "mods"
        self.shapes = {}
        monkeypatch.setattr(zb, "_nif_shapes", self._read)
        zb._CACHE.clear()
        zb._parse_sets.cache_clear()
        sets = self.mods / "Armour Mod" / "CalienteTools" / "BodySlide" / "SliderSets"
        sets.mkdir(parents=True)
        (sets / "Test Armor.osp").write_text(osp or _osp(), encoding="utf-8")
        sd = self.mods / "Armour Mod" / "CalienteTools" / "BodySlide" / "ShapeData" / "Test Armor"
        sd.mkdir(parents=True)
        self.nif(sd / "Test Armor.nif", Cuirass=CUIRASS, Pants=PANTS)
        OsdFile(1, [OsdMorph.from_offsets(n, rows) for n, rows in MORPHS.items()]
                ).save(sd / "Test Armor.osd")
        self.dirs = [self.mods / "Output", self.mods / "Armour Mod"]

    def _read(self, p):
        got = self.shapes.get(str(Path(p)).lower())
        if got is None:
            raise RuntimeError(f"cannot open {p}")
        return {n: v.copy() for n, v in got.items()}

    def nif(self, path: Path, **shapes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"nif")
        self.shapes[str(path).lower()] = {n: np.asarray(v, dtype=np.float64)
                                          for n, v in shapes.items()}

    def built(self, **per_weight):
        files = {}
        for w in ("_0", "_1"):
            f = self.mods / "Output" / "meshes" / "armor" / "test" / f"cuirass{w}.nif"
            self.nif(f, **per_weight.get(w, _zeroed(w)))
            files[w] = f
        return files

    def check(self, files):
        return zb.zeroed_garment(STEM, files, dirs=self.dirs)


# --- the garment check --------------------------------------------------------

def test_a_zeroed_armour_build_is_verified_at_both_weights(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    got = inst.check(inst.built())
    assert got.slider_set == "Test Armor"
    assert got.max_dev == pytest.approx(0.0, abs=1e-6)
    for w in ("_0", "_1"):
        assert set(got.build[w]) == {"Cuirass", "Pants"}
        np.testing.assert_allclose(got.build[w]["Cuirass"], _zeroed(w)["Cuirass"])


def test_a_preset_armour_build_is_refused(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    files = inst.built(_0=_zeroed("_0", bust=True), _1=_zeroed("_1", bust=True))
    with pytest.raises(zb.ZeroedBodyError, match="is not its zeroed build"):
        inst.check(files)


def test_a_build_ten_times_the_tolerance_off_is_refused(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    off = {w: _zeroed(w) for w in ("_0", "_1")}
    off["_1"]["Cuirass"][1] += (0.0, 10 * zb.GARMENT_TOL, 0.0)
    with pytest.raises(zb.ZeroedBodyError):
        inst.check(inst.built(**off))


def test_single_precision_round_off_is_still_the_zeroed_build(tmp_path, monkeypatch):
    """Real weight-0 builds sat up to 1.39e-4 off -- BodySlide adds the seam
    defaults in float32 -- and are the zeroed build all the same."""
    inst = Instance(tmp_path, monkeypatch)
    near = {w: _zeroed(w) for w in ("_0", "_1")}
    near["_0"]["Cuirass"][0] += (0.0, 0.0, 1.4e-4)
    assert inst.check(inst.built(**near)).max_dev == pytest.approx(1.4e-4, rel=1e-3)


def test_one_weight_off_refuses_the_pair(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    files = inst.built(_1=_zeroed("_1", bust=True))
    with pytest.raises(zb.ZeroedBodyError):
        inst.check(files)


def test_indices_past_the_shape_are_skipped_as_bodyslide_does(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    got = inst.check(inst.built())
    np.testing.assert_allclose(got.build["_1"]["Pants"][1], PANTS[1] + (0.5, 0.0, 0.0))


def test_a_default_on_zap_deletes_its_vertices(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    assert len(inst.check(inst.built()).build["_0"]["Pants"]) == len(PANTS) - 1
    kept = {w: {**_zeroed(w), "Pants": PANTS + (0.0, 0.0, 0.0)} for w in ("_0", "_1")}
    for w in kept:
        kept[w]["Pants"][1] += (0.5, 0.0, 0.0)
    with pytest.raises(zb.ZeroedBodyError):
        inst.check(inst.built(**kept))


def test_a_build_with_an_extra_shape_is_refused(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    extra = {w: {**_zeroed(w), "Belt": np.zeros((3, 3))} for w in ("_0", "_1")}
    with pytest.raises(zb.ZeroedBodyError):
        inst.check(inst.built(**extra))


def test_a_partly_on_zap_cannot_be_checked(tmp_path, monkeypatch):
    osp = _osp().replace('zap="true" small="100" big="100"', 'zap="true" small="50" big="50"')
    inst = Instance(tmp_path, monkeypatch, osp=osp)
    with pytest.raises(zb.ZeroedBodyError, match="partly on"):
        inst.check(inst.built())


def test_nothing_builds_an_unknown_piece(tmp_path, monkeypatch):
    inst = Instance(tmp_path, monkeypatch)
    files = inst.built()
    with pytest.raises(zb.ZeroedBodyError, match="no BodySlide slider set"):
        zb.zeroed_garment("armor/other/cuirass", files, dirs=inst.dirs)


def test_the_body_build_stays_strict(tmp_path, monkeypatch):
    """The body resolver's check is vertex for vertex against the game file, so
    it still refuses what the garment build tolerates."""
    import xml.etree.ElementTree as ET
    inst = Instance(tmp_path, monkeypatch)
    ss = next(ET.fromstring(_osp()).iter("SliderSet"))
    vfs = zb._Vfs(inst.dirs)
    shapes = zb._set_base(vfs, ss)[1]
    with pytest.raises(zb.ZeroedBodyError, match="zap slider"):
        zb._apply_defaults(vfs, ss, "_1", {"Pants": PANTS.copy()}, shapes, {})
    no_zap = ET.fromstring(_osp().replace('zap="true" ', ''))
    ss2 = next(no_zap.iter("SliderSet"))
    with pytest.raises(zb.ZeroedBodyError, match="indexes past the base shape"):
        zb._apply_defaults(vfs, ss2, "_1", {"Pants": PANTS.copy()}, shapes, {})


# --- the selection -------------------------------------------------------------

BASE_MOD, OUT_MOD = "Base Armour", "Example - Bodyslide Output - 3BA"


def _touch(p: Path, data=b"nif"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class Selection:
    """A base mod and the user's 3BA BodySlide output both providing one piece;
    the zeroed-build verdict, today's geometry and the provider are stubbed."""

    def __init__(self, root: Path, monkeypatch, *, verified=True, already=False,
                 provider=OUT_MOD):
        self.mods = root / "mods"
        self.calls = []
        for w in ("_0", "_1"):
            _touch(self.mods / BASE_MOD / "meshes" / f"{STEM}{w}.nif")
            _touch(self.mods / OUT_MOD / "meshes" / f"{STEM}{w}.nif")
        build = {w: _zeroed(w) for w in ("_0", "_1")}

        def fake_check(stem, files, dirs=None):
            self.calls.append(stem)
            if not verified:
                raise zb.ZeroedBodyError("not a zeroed build")
            return zb.ZeroedGarment("Test Armor", 0.0, build)

        monkeypatch.setattr(zb, "zeroed_garment", fake_check)
        monkeypatch.setattr(zb, "_layout_dirs", lambda: [])
        monkeypatch.setattr(zb, "_nif_shapes", lambda p: (
            build["_0" if str(p).endswith("_0.nif") else "_1"] if already
            else {"Cuirass": CUIRASS, "Pants": PANTS}))
        monkeypatch.setattr(discovery, "_zeroed_output_provider",
                            lambda *a: (provider, "" if provider else "no zeroed body"))
        monkeypatch.delenv("CBBE2UBE_NO_ZEROED_OUTPUT_SOURCE", raising=False)
        discovery._ZOS_SAID.clear()

    def index(self, order=(OUT_MOD, BASE_MOD)):
        keys = {f"{STEM}{w}.nif" for w in ("_0", "_1")}
        return discovery.build_mesh_index(self.mods, list(order), target_keys=keys)

    @staticmethod
    def owners(idx):
        return {k: v.parents[3].name for k, v in idx.items()}


def test_a_mods_own_meshes_give_way_to_the_verified_zeroed_build(tmp_path, monkeypatch):
    sel = Selection(tmp_path, monkeypatch)
    assert set(Selection.owners(sel.index()).values()) == {OUT_MOD}
    assert sel.calls == [STEM]


def test_an_unverified_build_keeps_todays_source(tmp_path, monkeypatch):
    sel = Selection(tmp_path, monkeypatch, verified=False)
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_piece_whose_physics_would_change_keeps_todays_source(tmp_path, monkeypatch):
    sel = Selection(tmp_path, monkeypatch)
    for w in ("_0", "_1"):
        _touch(sel.mods / OUT_MOD / "meshes" / f"{STEM}{w}.nif",
               b"nif HDT Skinned Mesh Physics Object x.xml")
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_source_that_already_is_the_zeroed_build_is_left_alone(tmp_path, monkeypatch):
    sel = Selection(tmp_path, monkeypatch, already=True)
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_the_output_must_build_both_weights(tmp_path, monkeypatch):
    sel = Selection(tmp_path, monkeypatch)
    (sel.mods / OUT_MOD / "meshes" / f"{STEM}_0.nif").unlink()
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert sel.calls == []


def test_only_a_mods_own_meshes_are_replaced(tmp_path, monkeypatch):
    """A piece today's tiers already take from a BodySlide output (here a UBE
    build, with no base mod) is not this rule's to move."""
    sel = Selection(tmp_path, monkeypatch)
    ube = "Example - Bodyslide Output - UBE"
    for w in ("_0", "_1"):
        (sel.mods / BASE_MOD / "meshes" / f"{STEM}{w}.nif").unlink()
        _touch(sel.mods / ube / "meshes" / f"{STEM}{w}.nif")
    assert set(Selection.owners(sel.index(order=(ube, OUT_MOD))).values()) == {ube}
    assert sel.calls == []


def test_the_off_switch_leaves_the_tiers_alone(tmp_path, monkeypatch):
    sel = Selection(tmp_path, monkeypatch)
    monkeypatch.setenv("CBBE2UBE_NO_ZEROED_OUTPUT_SOURCE", "1")
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert sel.calls == []


def test_no_provider_means_no_change(tmp_path, monkeypatch):
    sel = Selection(tmp_path, monkeypatch, provider=None)
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert sel.calls == []


# --- which output may provide --------------------------------------------------

def _provider_setup(tmp_path, monkeypatch, ref_for=None, refs=True):
    mods = tmp_path / "mods"
    body_dir = mods / OUT_MOD / "meshes" / "actors" / "character" / "character assets"
    bodies = {w: _touch(body_dir / f"femalebody{w}.nif") for w in ("_0", "_1")}
    monkeypatch.setattr(zb, "zeroed_body", lambda kind, w="_1", **k: zb.ZeroedBody(
        bodies[w], "3BA", "Test Body", 0.0))
    monkeypatch.setattr(br, "_find_cbbe_base_body",
                        lambda w="_1": (ref_for or bodies)[w])
    monkeypatch.setattr(br, "ZEROED_BODY_REFS", refs)
    return mods


def test_the_provider_is_the_folder_of_the_fits_zeroed_cbbe_body(tmp_path, monkeypatch):
    mods = _provider_setup(tmp_path, monkeypatch)
    assert discovery._zeroed_output_provider(mods, [OUT_MOD, BASE_MOD], set()) == (OUT_MOD, "")


def test_no_provider_when_the_fit_uses_another_cbbe_body(tmp_path, monkeypatch):
    other = {w: _touch(tmp_path / "picked" / f"femalebody{w}.nif") for w in ("_0", "_1")}
    mods = _provider_setup(tmp_path, monkeypatch, ref_for=other)
    got, why = discovery._zeroed_output_provider(mods, [OUT_MOD, BASE_MOD], set())
    assert got is None and "not the zeroed build" in why


def test_no_provider_when_zeroed_references_are_off(tmp_path, monkeypatch):
    mods = _provider_setup(tmp_path, monkeypatch, refs=False)
    got, why = discovery._zeroed_output_provider(mods, [OUT_MOD, BASE_MOD], set())
    assert got is None and "off" in why


def test_no_provider_when_the_body_mod_is_not_read(tmp_path, monkeypatch):
    mods = _provider_setup(tmp_path, monkeypatch)
    got, _ = discovery._zeroed_output_provider(mods, [BASE_MOD], set())
    assert got is None
