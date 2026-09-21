"""The zeroed-body resolver: the game-loaded body, verified to be BodySlide's
zeroed build, found by what the slider set builds -- never by mod name.

The base fixture mirrors the case that broke name-based discovery (2026-09-21):
the body mod ships the slider set, the ShapeData AND a femalebody built at some
preset; a higher-priority mod named like a BodySlide output ships the zeroed
build the game actually loads. Only NIF reading is stubbed; the slider-set XML
and the OSD files are real.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import zeroed_body as zb  # noqa: E402
from src.osd import OsdFile, OsdMorph  # noqa: E402

BODY_DIR = "meshes/actors/character/character assets"
BASE = np.array([[float(i), 0.0, 0.0] for i in range(6)])
SMALL = np.array([[0.0, 0.0, float(i)] for i in range(3)])   # a 3BA_Anus-like extra shape
MORPHS = {                                  # name -> (vertex, delta)
    "BodySeam": (0, (0.0, 0.0, 1.0)),       # default small=100 big=0  -> weight 0 only
    "BodyNip": (1, (0.0, 0.5, 0.0)),        # default small=100 big=100 -> both weights
    "BodyBust": (2, (0.0, 2.0, 0.0)),       # default 0/0 -> only a PRESET moves it
    "BodyInv": (3, (0.0, 0.0, 3.0)),        # inverted, 100/100 -> applies NOTHING
    "BodyUv": (4, (0.0, 0.0, 4.0)),         # uv slider -> moves no vertex
    "BodyHalf": (5, (1.0, 0.0, 0.0)),       # default 50/50 -> HALF the delta
}


def _data(morph, target="Body", attrs='local="true"', osd="Test Body.osd"):
    return f'<Data name="{morph}" target="{target}" {attrs}>{osd}\\{morph}</Data>'


def _set(name="Test Body", folder="Test Body", output="femalebody",
         shape='<Shape target="Body">Body</Shape>', target="Body",
         nip_big="100", attrs='local="true"', extra=""):
    d = lambda m: _data(m, target, attrs)
    return f"""  <SliderSet name="{name}">
    <DataFolder>{folder}</DataFolder>
    <SourceFile>Test Body.nif</SourceFile>
    <OutputPath>meshes\\actors\\character\\character assets</OutputPath>
    <OutputFile GenWeights="true">{output}</OutputFile>
    {shape}
    <Shape target="Body_Small">Body_Small</Shape>
    <Slider name="Seam" invert="false" small="100" big="0">{d("BodySeam")}</Slider>
    <Slider name="Nip" invert="false" small="100" big="{nip_big}">{d("BodyNip")}</Slider>
    <Slider name="Bust" invert="false" small="0" big="0">{d("BodyBust")}</Slider>
    <Slider name="Inv" invert="true" small="100" big="100">{d("BodyInv")}</Slider>
    <Slider name="Uv" invert="false" small="100" big="100" uv="true">{d("BodyUv")}</Slider>
    <Slider name="Half" invert="false" small="50" big="50">{d("BodyHalf")}</Slider>
    {extra}
  </SliderSet>"""


def _osp(*sets):
    return ('﻿<?xml version="1.0" encoding="UTF-8"?>\n<SliderSetInfo version="1">\n'
            + "\n".join(sets) + "\n</SliderSetInfo>\n")


def _moved(*names, half=True):
    v = BASE.copy()
    for n in names + (("BodyHalf",) if half else ()):
        i, d = MORPHS[n]
        v[i] += (0.5 if n == "BodyHalf" else 1.0) * np.array(d)
    return v


ZEROED = {"_0": _moved("BodySeam", "BodyNip"), "_1": _moved("BodyNip")}
PRESET = {"_0": _moved("BodySeam", "BodyNip", "BodyBust"),
          "_1": _moved("BodyNip", "BodyBust")}


class Modlist:
    """A mods tree plus the shapes each fake NIF holds."""

    def __init__(self, root: Path, monkeypatch, osp_text=None, folder="Test Body"):
        self.base = root
        self.root = root / "mods"
        self.shapes = {}
        monkeypatch.setitem(zb.KINDS, "cbbe", (BODY_DIR, "femalebody", len(BASE), "CBBE 3BA"))
        monkeypatch.setattr(zb, "_shape_verts",
                            lambda p, s: self.shapes.get(self._k(p), {}).get(s))
        monkeypatch.setattr(zb, "_shape_sizes", self._sizes)
        zb._CACHE.clear()
        self.slider_sets("Body Mod", osp_text or _osp(_set()))
        self.shapedata("Body Mod", folder)

    @staticmethod
    def _k(p):
        return str(Path(p)).lower()

    def _sizes(self, p):
        got = self.shapes.get(self._k(p))
        if got is None:                         # like pynifly on a file it cannot read
            raise RuntimeError(f"cannot open {p}")
        return {n: len(v) for n, v in got.items()}

    def nif(self, path: Path, **shapes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"nif")
        self.shapes[self._k(path)] = {n: np.asarray(v, dtype=np.float64)
                                      for n, v in shapes.items()}

    def slider_sets(self, mod, text, name="Test Body.osp", sub=""):
        d = self.root / mod / "CalienteTools" / "BodySlide" / "SliderSets" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    def shapedata(self, mod, folder="Test Body", base=True):
        sd = self.root / mod / "CalienteTools" / "BodySlide" / "ShapeData" / folder
        sd.mkdir(parents=True, exist_ok=True)
        if base:
            self.nif(sd / "Test Body.nif", Body=BASE, Body_Small=SMALL)
        OsdFile(1, [OsdMorph.from_offsets(n, [(i, *d)])
                    for n, (i, d) in MORPHS.items()]).save(sd / "Test Body.osd")
        return sd

    def body(self, mod, builds, weights=("_0", "_1"), shape="Body", where=None):
        for w in weights:
            self.nif((where or self.root / mod) / BODY_DIR / f"femalebody{w}.nif",
                     **{shape: builds[w]})

    def resolve(self, order, weight, **kw):
        return zb.zeroed_body("cbbe", weight, mods_root=self.root, order=order, **kw)


def test_resolves_the_game_loaded_zeroed_body_at_each_weight(tmp_path, monkeypatch):
    """The zeroed build in a mod NAMED like a BodySlide output wins over the
    body mod's own preset femalebody -- the exact case name-based discovery
    got backwards. The body is the LARGEST shape of the set, not an extra one."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Body Mod", PRESET)
    ml.body("My BodySlide Output - 3BA", ZEROED)
    order = ["My BodySlide Output - 3BA", "Body Mod"]
    for w in ("_0", "_1"):
        got = ml.resolve(order, w)
        assert got.path.parents[4].name == "My BodySlide Output - 3BA"
        assert got.path.name == f"femalebody{w}.nif"
        assert (got.shape, got.slider_set) == ("Body", "Test Body")
        assert got.max_dev <= zb.TOL


def test_weight0_is_built_with_the_small_defaults(tmp_path, monkeypatch):
    """A weight-0 file WITHOUT the weight-0-only default is not zeroed, while
    the same garment set passes at weight 1 -- small and big are not mixed."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", {"_0": ZEROED["_1"], "_1": ZEROED["_1"]})
    assert ml.resolve(["Out", "Body Mod"], "_1").max_dev <= zb.TOL
    with pytest.raises(zb.ZeroedBodyError, match="NOT a zeroed"):
        ml.resolve(["Out", "Body Mod"], "_0")


def test_a_preset_build_the_game_loads_is_refused(tmp_path, monkeypatch):
    """When the top-priority file carries a preset, the answer is an error --
    never the lower-priority zeroed file the game does not load."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Body Mod", PRESET)
    ml.body("Out", ZEROED)
    with pytest.raises(zb.ZeroedBodyError, match="off by up to 2.000u on 1 of 6"):
        ml.resolve(["Body Mod", "Out"], "_1")


def test_a_build_ten_times_the_tolerance_off_is_refused(tmp_path, monkeypatch):
    """TOL is a round-off allowance, not a slack: a near-zeroed preset is not
    the reference."""
    ml = Modlist(tmp_path, monkeypatch)
    near = {w: v.copy() for w, v in ZEROED.items()}
    near["_1"][2] += (0.0, 10 * zb.TOL, 0.0)
    ml.body("Out", near)
    with pytest.raises(zb.ZeroedBodyError, match="NOT a zeroed"):
        ml.resolve(["Out", "Body Mod"], "_1")


def test_inverted_uv_and_fractional_defaults_build_the_way_bodyslide_does(tmp_path, monkeypatch):
    """invert at 100 applies nothing, a uv slider moves no vertex, and a 50
    default applies half the delta -- ZEROED is built with exactly that."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", ZEROED)
    assert ml.resolve(["Out", "Body Mod"], "_1").max_dev <= zb.TOL
    wrong = {"_0": ZEROED["_0"], "_1": _moved("BodyNip", "BodyInv")}   # inversion ignored
    ml.body("Out2", wrong)
    with pytest.raises(zb.ZeroedBodyError):
        ml.resolve(["Out2", "Body Mod"], "_1")


def test_the_shape_is_named_as_bodyslide_names_it(tmp_path, monkeypatch):
    """BodySlide writes the base NIF's shape NAME into the build, not the
    slider-set target (plain CBBE SE declares target BaseShapeHR, name CBBE).
    The game file is read, and the shape reported, by that NAME."""
    text = _osp(_set(shape='<Shape target="BodyT">Body</Shape>', target="BodyT"))
    ml = Modlist(tmp_path, monkeypatch, osp_text=text)
    ml.body("Out", ZEROED)
    got = ml.resolve(["Out", "Body Mod"], "_1")
    assert got.shape == "Body"


def test_another_body_family_at_the_same_path_is_refused(tmp_path, monkeypatch):
    """A BHUNP- or CBBE-SE-like body builds the same femalebody path. Its own
    zeroed build must NOT come back as the 3BA reference, even when it matches."""
    other = _set(name="Other Body", folder="Other Body")
    ml = Modlist(tmp_path, monkeypatch, osp_text=_osp(other))
    od = ml.shapedata("Body Mod", "Other Body", base=False)
    seven = np.vstack([BASE, [[9.0, 9.0, 9.0]]])
    ml.nif(od / "Test Body.nif", Body=seven, Body_Small=SMALL)
    fam = {"_0": np.vstack([ZEROED["_0"], [[9.0, 9.0, 9.0]]]),
           "_1": np.vstack([ZEROED["_1"], [[9.0, 9.0, 9.0]]])}
    ml.body("Out", fam)
    with pytest.raises(zb.ZeroedBodyError, match="7-vertex body, not CBBE 3BA"):
        ml.resolve(["Out", "Body Mod"], "_1")


def test_a_broken_unrelated_slider_set_does_not_stop_the_search(tmp_path, monkeypatch):
    """A damaged set that sorts first (an OSD that is not an OSD) is recorded
    and skipped; the good set still resolves -- no raw exception escapes."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.slider_sets("Broken Mod", _osp(_set(name="Broken", folder="Broken")),
                   name="AAA Broken.osp")
    bd = ml.root / "Broken Mod" / "CalienteTools" / "BodySlide" / "ShapeData" / "Broken"
    bd.mkdir(parents=True)
    ml.nif(bd / "Test Body.nif", Body=BASE, Body_Small=SMALL)
    (bd / "Test Body.osd").write_bytes(b"not an osd file at all")
    ml.body("Out", ZEROED)
    got = ml.resolve(["Out", "Broken Mod", "Body Mod"], "_1")
    assert got.slider_set == "Test Body"


def test_an_unreadable_game_file_is_an_error_callers_skip_on(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Out", ZEROED)
    ml.shapes.pop(ml._k(ml.root / "Out" / BODY_DIR / "femalebody_1.nif"))
    with pytest.raises(FileNotFoundError, match="unreadable"):
        ml.resolve(["Out", "Body Mod"], "_1")


def test_the_winning_copy_of_a_slider_set_file_is_read(tmp_path, monkeypatch):
    """A higher-priority mod overriding the .osp changes the defaults; BodySlide
    reads that copy, so the zeroed build is built from it."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.slider_sets("Patch", _osp(_set(nip_big="0")))
    base_only = {"_0": ZEROED["_0"], "_1": _moved()}
    ml.body("Out", base_only)
    assert ml.resolve(["Out", "Patch", "Body Mod"], "_1").max_dev <= zb.TOL


def test_slider_sets_in_subfolders_are_found(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch, osp_text=_osp(_set(name="Unused", output="elsewhere")))
    ml.slider_sets("Body Mod", _osp(_set()), name="Nested.osp", sub="Bodies")
    ml.body("Out", ZEROED)
    assert ml.resolve(["Out", "Body Mod"], "_1").slider_set == "Test Body"


def test_shared_slider_data_is_read_from_the_shapes_folder(tmp_path, monkeypatch):
    """Non-local data lives in the <Shape DataFolder>, as BodySlide reads it."""
    text = _osp(_set(folder="Variant", attrs="",
                     shape='<Shape target="Body" DataFolder="Shared Body">Body</Shape>'))
    ml = Modlist(tmp_path, monkeypatch, osp_text=text, folder="Shared Body")
    vd = ml.root / "Body Mod" / "CalienteTools" / "BodySlide" / "ShapeData" / "Variant"
    vd.mkdir(parents=True)
    ml.nif(vd / "Test Body.nif", Body=BASE, Body_Small=SMALL)
    ml.body("Out", ZEROED)
    assert ml.resolve(["Out", "Body Mod"], "_1").max_dev <= zb.TOL


def test_overwrite_beats_every_mod_and_game_data_comes_last(tmp_path, monkeypatch):
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Body Mod", PRESET)
    ow = tmp_path / "overwrite"
    ml.body(None, ZEROED, where=ow)
    got = ml.resolve(["Body Mod"], "_1", overwrite=ow)
    assert got.path.parents[4].name.lower() == "overwrite"
    # no mod ships the body any more: the game Data folder is what is left
    for w in ("_0", "_1"):
        (ml.root / "Body Mod" / BODY_DIR / f"femalebody{w}.nif").unlink()
    data = tmp_path / "Data"
    ml.body(None, ZEROED, where=data)
    got = ml.resolve(["Body Mod"], "_1", data_dirs=[data])
    assert got.path.parents[4].name == "Data"


def test_a_half_installed_pair_is_refused(tmp_path, monkeypatch):
    """Weight 0 must come from the same folder as weight 1."""
    ml = Modlist(tmp_path, monkeypatch)
    ml.body("Body Mod", ZEROED)
    ml.body("Out", ZEROED, weights=("_0",))
    with pytest.raises(zb.ZeroedBodyError, match="half-installed"):
        ml.resolve(["Out", "Body Mod"], "_0")


def test_no_slider_set_and_no_body_are_errors_callers_already_skip_on(tmp_path, monkeypatch):
    """Both failures are FileNotFoundError: the census and the nipple scorer
    already read that as 'no reference body -- skip, never substitute'."""
    ml = Modlist(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError, match="nothing in the load order"):
        ml.resolve(["Body Mod"], "_1")
    ml.body("Out", ZEROED)
    (ml.root / "Body Mod" / "CalienteTools" / "BodySlide" / "SliderSets"
     / "Test Body.osp").unlink()
    with pytest.raises(FileNotFoundError, match="no BodySlide slider set"):
        ml.resolve(["Out", "Body Mod"], "_1")


def test_a_half_given_instance_is_refused(tmp_path):
    with pytest.raises(ValueError, match="together"):
        zb.zeroed_body("cbbe", "_1", mods_root=tmp_path)
