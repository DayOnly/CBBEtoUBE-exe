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

"""Silent-failure surfacing: a NIF that converts but DROPS a shape (the piece is
absent/invisible in-game) must be counted + reported as a PARTIAL conversion,
not folded into a clean 'converted' success with no signal."""
from pathlib import Path

from src.nif_convert import ConvertResult
from src.auto_convert import AutoConvertResult


def _cr(name, dropped=None):
    return ConvertResult(src_path=Path(name), dst_path=Path(name + ".out"),
                         status="converted (copy)", dropped_shapes=dropped or [])


def test_nif_partial_counts_only_dropped():
    acr = AutoConvertResult(
        source_dir=Path("."), output_dir=Path("."),
        nif_results=[_cr("a", ["X"]), _cr("b"), _cr("c", ["Y", "Z"])])
    assert acr.nif_partial == 2
    # a clean converted NIF must NOT be counted partial
    assert AutoConvertResult(source_dir=Path("."), output_dir=Path("."),
                             nif_results=[_cr("b")]).nif_partial == 0


def test_write_report_surfaces_partial_bucket(tmp_path):
    acr = AutoConvertResult(
        source_dir=tmp_path, output_dir=tmp_path,
        nif_results=[_cr(str(tmp_path / "armor.nif"), ["ShoulderStrap"])])
    rep = tmp_path / "report.txt"
    acr.write_report(rep)
    txt = rep.read_text()
    assert "PARTIAL conversions" in txt
    assert "ShoulderStrap" in txt


def test_write_report_no_partial_bucket_when_clean(tmp_path):
    acr = AutoConvertResult(
        source_dir=tmp_path, output_dir=tmp_path,
        nif_results=[_cr(str(tmp_path / "armor.nif"))])
    rep = tmp_path / "report.txt"
    acr.write_report(rep)
    assert "PARTIAL conversions" not in rep.read_text()


def test_drain_result_surfaces_dead_worker():
    # A worker PROCESS death (native pynifly crash -> BrokenProcessPool) must
    # become a recorded error ConvertResult, not an exception that aborts the
    # whole batch and loses the report.
    from src.auto_convert import _drain_result
    from concurrent.futures.process import BrokenProcessPool

    class _DeadFut:
        def result(self):
            raise BrokenProcessPool(
                "A process in the process pool was terminated abruptly")

    item = ("C:/x/boots_0.nif", "C:/out/boots_0.nif", None, 0)
    r = _drain_result(_DeadFut(), item)
    assert r.status == "error"
    assert r.src_path == item[0]
    assert "died" in r.reason.lower()


def test_drain_result_passthrough_ok():
    # A normal future result is returned unchanged (happy path untouched).
    from src.auto_convert import _drain_result

    class _OkFut:
        def result(self):
            return _cr("ok")

    assert _drain_result(_OkFut(), ("a", "b", None, 0)).status == "converted (copy)"


def test_merge_clears_stale_esm_tier_cache(tmp_path):
    # The merge must classify masters FRESH: _is_esm_tier_master consults the
    # cache before the on-disk flag read, so a stale verdict (an ESL-flagged .esp
    # mis-cached as 'regular' during the per-source phase) would mis-sort it after
    # a regular master -> ESL-after-regular = load CTD. merge_patches_split clears
    # the cache at its start so a poisoned entry can't survive into the sort.
    import struct
    from src import esp, ube_patcher
    from src.esp import encode_subrecord, encode_zstring

    ube_patcher.clear_esm_tier_cache()
    ube_patcher._ESM_TIER_CACHE["poison.esp"] = False    # stale 'regular' verdict
    arma = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                      version_unk=0x2C,
                      payload=(encode_subrecord(b"EDID", encode_zstring("A"))
                               + encode_subrecord(b"RNAM", struct.pack("<I", 0x19))))
    patch = tmp_path / "Mod UBE patch.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[arma])]).save(patch)
    ube_patcher.merge_patches_split([patch], tmp_path / "Combined.esp",
                                    esl_flag=True)
    assert "poison.esp" not in ube_patcher._ESM_TIER_CACHE, \
        "merge_patches_split did not clear the stale ESM-tier cache"


def test_postflight_validate_combined_classifies_ctd(tmp_path):
    # The postflight re-validates the FINAL Combined; a load-breaking issue
    # (formid-zero) must be classed CTD, and a clean plugin must produce no CTD.
    import struct
    from src import esp
    from src.esp import encode_subrecord, encode_zstring
    from src.ube_patcher import postflight_validate_combined

    def _save(path, fid):
        payload = (encode_subrecord(b"EDID", encode_zstring("XA"))
                   + encode_subrecord(b"RNAM", struct.pack("<I", 0x19)))
        arma = esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                          version_unk=0x2C, payload=payload)
        esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=[arma])]).save(path)

    combined = tmp_path / "Combined.esp"
    _save(combined, 0x00000000)              # FormID 0 -> "formid-zero" (CTD-class)
    pf = postflight_validate_combined(combined)
    assert combined.name in pf["pieces"]
    assert any("formid-zero" in w for _, w in pf["ctd"]), pf

    clean = tmp_path / "Clean.esp"
    _save(clean, 0x01000800)                 # valid own FormID
    pf2 = postflight_validate_combined(clean)
    assert not pf2["ctd"], pf2


def test_nif_invariant_issues_flags_zerovert_and_overcap():
    # Postflight per-NIF invariants: a zero-vert shape (invisible) and an over-cap
    # shape left in <=1 partition (split failed -> CTD) are flagged; a properly
    # split over-cap shape and a small shape are not.
    from src.auto_convert import _nif_invariant_issues

    class _S:
        def __init__(self, name, nverts, nbones, nparts):
            self.name = name
            self.verts = [0] * nverts
            self.bone_names = ["b"] * nbones
            self.partitions = [object()] * nparts

    shapes = [
        _S("Zero", 0, 4, 1),         # zero-vert -> flagged
        _S("Overcap", 100, 90, 1),   # 90 bones, 1 partition -> split failed
        _S("OkSplit", 100, 90, 2),   # 90 bones but 2 partitions -> fine
        _S("Small", 100, 10, 1),     # under cap -> fine
    ]
    issues = _nif_invariant_issues("armor_1.nif", shapes, cap=78)
    joined = " ".join(issues)
    assert "Zero" in joined and "ZERO-vertex" in joined
    assert "Overcap" in joined and "split failed" in joined
    assert "OkSplit" not in joined
    assert "Small" not in joined
    assert len(issues) == 2


def test_postflight_missing_weight_partners(tmp_path):
    # Detect a body mesh with a _0 but no _1 across the whole tree; ignore a
    # complete pair and a weight-agnostic (no _0/_1) mesh.
    from src.auto_convert import _postflight_missing_weight_partners
    d = tmp_path / "meshes" / "armor"
    d.mkdir(parents=True)
    (d / "boots_0.nif").write_bytes(b"x")     # _0 only -> missing _1
    (d / "gloves_0.nif").write_bytes(b"x")    # complete pair
    (d / "gloves_1.nif").write_bytes(b"x")
    (d / "helmet.nif").write_bytes(b"x")      # weight-agnostic -> ignored
    miss = _postflight_missing_weight_partners(tmp_path)
    joined = " ".join(miss)
    assert "boots_0.nif" in joined and "_1 MISSING" in joined
    assert "gloves" not in joined
    assert "helmet" not in joined
    assert len(miss) == 1


def test_esp_gen_failures_field_defaults_empty():
    acr = AutoConvertResult(source_dir=Path("."), output_dir=Path("."))
    assert acr.esp_gen_failures == []


def test_esp_skipped_no_armor_field_defaults_zero():
    acr = AutoConvertResult(source_dir=Path("."), output_dir=Path("."))
    assert acr.esp_skipped_no_armor == 0


def test_esp_with_no_arma_group_is_detectable(tmp_path):
    # The per-ESP pre-filter skips a source ESP when `group(b"ARMA") is None`
    # (no armor addons -> nothing to convert -> NOT a failure). Confirm that
    # predicate holds for an armor-free ESP and not for one with armatures.
    import struct
    from src import esp
    from src.esp import encode_subrecord, encode_zstring

    def _armo(fid):
        payload = (encode_subrecord(b"EDID", encode_zstring("X"))
                   + encode_subrecord(b"FULL", encode_zstring("X")))
        return esp.Record(sig=b"ARMO", flags=0, formid=fid, timestamp_vc=0,
                          version_unk=0x2C, payload=payload)

    def _arma(fid):
        payload = (encode_subrecord(b"EDID", encode_zstring("XA"))
                   + encode_subrecord(b"RNAM", struct.pack("<I", 0x19)))
        return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                          version_unk=0x2C, payload=payload)

    no_armor = tmp_path / "patch.esp"        # landscape/quest/patch-like: ARMO only
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMO", records=[_armo(0x01000800)])]
            ).save(no_armor)
    has_armor = tmp_path / "armor.esp"
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[_arma(0x01000801)])]
            ).save(has_armor)

    assert esp.ESP.load_cached(no_armor).group(b"ARMA") is None     # -> skipped
    assert esp.ESP.load_cached(has_armor).group(b"ARMA") is not None  # -> patched
