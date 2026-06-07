"""Regression tests for ube_patcher.fold_ube_raceskin_skins.

Builds synthetic minimal UBE_AllRace + Vanilla_UBE_Race_Compat ESPs (no
external game files) and verifies the fold:
  * mints primary-race UBE skin ARMAs (Torso/Hands/Feet) for every race in
    the template's additional-races list, pointing at the !UBE meshes;
  * overrides 00UBE_SkinNaked, prepending the new armatures BEFORE the DATA
    subrecord (Skyrim stops reading armatures at DATA);
  * remaps FormIDs from UBE_AllRace's master order into the VC's;
  * declares UBE_AllRace's full transitive-master closure (adds a missing
    master + remaps existing records) so the override can't misroute;
  * is idempotent (no-op on a second run);
  * aborts (writes nothing) when the ESL FE-space ceiling (0xFFF) would be
    exceeded.
"""
import struct
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import esp
from src import ube_patcher as up

UBE_NAME = "UBE_AllRace.esp"
# UBE_AllRace own-space FormID LOW parts (top byte = its own master count).
BRETON_LOW = 0x801
REDGUARD_LOW = 0x802
TORSO_LOW = 0x810
HANDS_LOW = 0x811
FEET_LOW = 0x812
SKINNAKED_LOW = 0x820
VANILLA_ARMA = 0x00012345  # a Skyrim.esm racial skin armature in SkinNaked


def _z(s):
    return s.encode("ascii") + b"\x00"


def _sub(sig, data):
    return esp.encode_subrecord(sig, data)


def _arma(edid, rnam, mesh, slot_bit, addl):
    p = (_sub(b"EDID", _z(edid))
         + _sub(b"BOD2", struct.pack("<II", slot_bit, 0))
         + _sub(b"RNAM", struct.pack("<I", rnam))
         + _sub(b"DNAM", b"\x00" * 12)
         + _sub(b"MOD3", _z(mesh)))
    for f in addl:
        p += _sub(b"MODL", struct.pack("<I", f))
    return p


def _skinnaked(breton, armature_fids):
    p = (_sub(b"EDID", _z("00UBE_SkinNaked"))
         + _sub(b"OBND", b"\x00" * 12)
         + _sub(b"BOD2", struct.pack("<II", 0, 0))
         + _sub(b"RNAM", struct.pack("<I", breton)))
    for f in armature_fids:
        p += _sub(b"MODL", struct.pack("<I", f))
    p += _sub(b"DATA", struct.pack("<If", 0, 0.0))
    p += _sub(b"DNAM", struct.pack("<I", 0))
    return p


def _rec(sig, fid, payload):
    return esp.Record(sig=sig, flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x002C, payload=payload)


def _write_ube(path, extra_masters=()):
    """Synthetic UBE_AllRace. own top byte = master count; FormIDs derived
    so adding masters (the transitive-closure case) is exercised."""
    masters = ["Skyrim.esm"] + list(extra_masters)
    own = len(masters)
    breton = (own << 24) | BRETON_LOW
    redguard = (own << 24) | REDGUARD_LOW
    torso = (own << 24) | TORSO_LOW
    hands = (own << 24) | HANDS_LOW
    feet = (own << 24) | FEET_LOW
    skin = (own << 24) | SKINNAKED_LOW
    races = [breton, redguard]
    hdr = esp.TES4Header(masters=masters, author="t")
    arma = esp.Group(label=b"ARMA", records=[
        _rec(b"ARMA", torso, _arma(
            "00UBE_NakedTorso", breton,
            r"!UBE\Body\femalebody_tangent_1.nif", 1 << 2, races)),
        _rec(b"ARMA", hands, _arma(
            "00UBE_NakedHands", breton,
            r"!UBE\Hands\femalehands_tangent_1.nif", 1 << 3, races)),
        _rec(b"ARMA", feet, _arma(
            "00UBE_NakedFeet", breton,
            r"!UBE\Feet\femalefeet_tangent_1.nif", 1 << 7, races)),
    ])
    race_g = esp.Group(label=b"RACE", records=[
        _rec(b"RACE", breton, _sub(b"EDID", _z("00UBE_BretonRace"))),
        _rec(b"RACE", redguard, _sub(b"EDID", _z("00UBE_RedguardRace"))),
    ])
    armo = esp.Group(label=b"ARMO", records=[
        _rec(b"ARMO", skin,
             _skinnaked(breton, [torso, hands, feet, VANILLA_ARMA])),
    ])
    esp.ESP(header=hdr, groups=[arma, race_g, armo]).save(path)


def _write_vc(path, seed_own_low=0x800):
    # masters: Skyrim.esm(0), UBE_AllRace.esp(1) -> VC own top byte 2
    hdr = esp.TES4Header(masters=["Skyrim.esm", UBE_NAME], author="t",
                         flags=up.TES4_FLAG_ESL)
    own = 2
    arma = esp.Group(label=b"ARMA", records=[
        _rec(b"ARMA", (own << 24) | seed_own_low,
             _arma("SeedBodyARMA_UBE", (1 << 24) | BRETON_LOW,
                   r"!UBE\armor\iron\f\cuirass_1.nif", 1 << 2, [])),
    ])
    armo = esp.Group(label=b"ARMO", records=[])
    esp.ESP(header=hdr, groups=[arma, armo]).save(path)


def _load_groups(path):
    e = esp.ESP.load(path)
    g = {grp.label: grp for grp in e.groups}
    return e, g


def test_fold_routes_primary_race_to_ube(tmp_path):
    ube = tmp_path / UBE_NAME
    vc = tmp_path / "Vanilla_UBE_Race_Compat.esp"
    _write_ube(ube)
    _write_vc(vc)

    res = up.fold_ube_raceskin_skins(vc, ube)
    assert res["folded"] == 6, res          # 2 races x 3 slots
    assert res["races"] == 2, res

    e, g = _load_groups(vc)
    own = len(e.header.masters)
    ube_idx = e.header.masters.index(UBE_NAME)

    # SkinNaked override present, FID remapped into VC space
    skins = [r for r in g[b"ARMO"].records if up._edid_of_rec(r) == "00UBE_SkinNaked"]
    assert len(skins) == 1
    assert skins[0].formid == (ube_idx << 24) | SKINNAKED_LOW

    # armatures: 4 original + 6 new, ALL before DATA
    seq = [s for s, _ in esp.iter_subrecords(skins[0].payload)]
    modl = [struct.unpack("<I", d)[0]
            for s, d in esp.iter_subrecords(skins[0].payload)
            if s == b"MODL" and len(d) == 4]
    assert len(modl) == 10, modl
    assert max(i for i, s in enumerate(seq) if s == b"MODL") < seq.index(b"DATA")

    # no dangling own-armature refs
    own_arma = {r.formid for r in g[b"ARMA"].records if (r.formid >> 24) == own}
    assert [f for f in modl if (f >> 24) == own and f not in own_arma] == []

    # the Redguard hands ARMA: primary RNAM = Redguard (remapped), mesh = !UBE
    rg_hands = [r for r in g[b"ARMA"].records
                if up._edid_of_rec(r) == "00UBE_NakedHands_Redguard"]
    assert len(rg_hands) == 1
    rnam = mesh = None
    for s, d in esp.iter_subrecords(rg_hands[0].payload):
        if s == b"RNAM":
            rnam = struct.unpack("<I", d)[0]
        elif s == b"MOD3":
            mesh = d.rstrip(b"\x00").decode("ascii")
    assert rnam == (ube_idx << 24) | REDGUARD_LOW
    assert mesh == r"!UBE\Hands\femalehands_tangent_1.nif"


def test_fold_adds_missing_transitive_master(tmp_path):
    ube = tmp_path / UBE_NAME
    vc = tmp_path / "Vanilla_UBE_Race_Compat.esp"
    # UBE masters Skyrim + RaceCompatibility; VC masters Skyrim + UBE
    # (lacks RaceCompatibility) -> closure must add it + remap existing records.
    _write_ube(ube, extra_masters=["RaceCompatibility.esm"])
    _write_vc(vc, seed_own_low=0x123)
    seed_low_before = 0x123

    res = up.fold_ube_raceskin_skins(vc, ube)
    assert res["folded"] == 6, res

    e, g = _load_groups(vc)
    ml = [m.lower() for m in e.header.masters]
    # RaceCompatibility added, before the ESP (ESM-before-ESP preserved)
    assert "racecompatibility.esm" in ml
    assert ml.index("racecompatibility.esm") < ml.index(UBE_NAME.lower())

    # the pre-existing seed body ARMA survived the remap: still own-space,
    # same low24 (only its top byte shifted with the new master count)
    own = len(e.header.masters)
    seed = [r for r in g[b"ARMA"].records
            if up._edid_of_rec(r) == "SeedBodyARMA_UBE"]
    assert len(seed) == 1
    assert (seed[0].formid >> 24) == own
    assert (seed[0].formid & 0xFFFFFF) == seed_low_before

    # validator now clean (RaceCompat declared -> no unmappable-master-ref;
    # SkinNaked exempt from armo-missing-full)
    warns = up.validate_patch(vc, check_nifs=False, master_data_dirs=[tmp_path])
    assert not [w for w in warns if "unmappable-master-ref" in w], warns
    assert not [w for w in warns if "armo-missing-full" in w], warns
    assert not [w for w in warns if "master-ordering" in w], warns


def test_fold_is_idempotent(tmp_path):
    ube = tmp_path / UBE_NAME
    vc = tmp_path / "Vanilla_UBE_Race_Compat.esp"
    _write_ube(ube)
    _write_vc(vc)
    assert up.fold_ube_raceskin_skins(vc, ube)["folded"] == 6
    again = up.fold_ube_raceskin_skins(vc, ube)
    assert again["folded"] == 0
    assert again["reason"] == "already present"


def test_fold_aborts_on_esl_ceiling(tmp_path):
    ube = tmp_path / UBE_NAME
    vc = tmp_path / "Vanilla_UBE_Race_Compat.esp"
    _write_ube(ube)
    # seed an own record so close to the 0xFFF ceiling that 6 new won't fit
    _write_vc(vc, seed_own_low=up.ESL_OWN_FORMID_MAX - 2)   # 0xFFD
    before = esp.ESP.load(vc)
    n_before = sum(len(grp.records) for grp in before.groups)

    res = up.fold_ube_raceskin_skins(vc, ube)
    assert res["folded"] == 0
    assert "ESL" in res["reason"]

    # nothing was written (no SkinNaked override, record count unchanged)
    after = esp.ESP.load(vc)
    n_after = sum(len(grp.records) for grp in after.groups)
    assert n_after == n_before
    assert not any(up._edid_of_rec(r) == "00UBE_SkinNaked"
                   for grp in after.groups if grp.label == b"ARMO"
                   for r in grp.records)


def test_fold_skips_when_vc_lacks_ube_master(tmp_path):
    ube = tmp_path / UBE_NAME
    vc = tmp_path / "Vanilla_UBE_Race_Compat.esp"
    _write_ube(ube)
    # VC that does NOT master UBE_AllRace
    hdr = esp.TES4Header(masters=["Skyrim.esm"], author="t",
                         flags=up.TES4_FLAG_ESL)
    esp.ESP(header=hdr, groups=[esp.Group(label=b"ARMA", records=[]),
                                esp.Group(label=b"ARMO", records=[])]).save(vc)
    res = up.fold_ube_raceskin_skins(vc, ube)
    assert res["folded"] == 0
    assert "UBE_AllRace" in res["reason"]


def test_is_nude_skin_model_classification():
    f = up._is_nude_skin_model
    # actor skin -> True (any "character assets" path OR nude basename prefix)
    assert f(r"Actors\Character\Character Assets\FemaleHands_1.nif")
    assert f(r"actors\character\character assets\ChildHands.nif")
    assert f(r"meshes\actors\character\character assets\femalebody_0.nif")
    assert f(r"!UBE\Hands\femalehands_tangent_1.nif")   # nude basename prefix
    assert f("FemaleHandsArgonian_1.nif")               # beast variant prefix
    # armor / clothes -> False (never under "character assets")
    assert not f(r"Armor\Iron\F\Gauntlets_1.nif")
    assert not f(r"Clothes\MageApprentice\MageApprenticeBootsF_1.nif")
    assert not f(r"!UBE\Kreis\KCO\mct\FeetM_1.nif")     # armor boot under !UBE
    assert not f("")


if __name__ == "__main__":
    import tempfile
    for fn in (test_fold_routes_primary_race_to_ube,
               test_fold_adds_missing_transitive_master,
               test_fold_is_idempotent,
               test_fold_aborts_on_esl_ceiling,
               test_fold_skips_when_vc_lacks_ube_master,
               test_is_nude_skin_model_classification):
        d = Path(tempfile.mkdtemp())
        fn(d)
        print(f"PASS {fn.__name__}")
    print("all raceskin-fold tests passed")
